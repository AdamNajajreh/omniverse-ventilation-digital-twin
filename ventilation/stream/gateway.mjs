// Password gate in front of the Kit stream. It serves the web client and carries
// Kit's signaling connection, so Kit's own port never has to face the internet.
//
//   node gateway.mjs --set-password    store a new password
//   node gateway.mjs                   run, behind an HTTPS proxy

import { createHmac, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import { createReadStream, existsSync, mkdirSync, readFileSync, statSync, utimesSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PORT = 8080;
const KIT_SIGNAL_PORT = 49100;
const STATE_DIR = path.join(os.homedir(), '.local/state/ventilation-stream');
const PASSWORD_FILE = path.join(STATE_DIR, 'password');
const SECRET_FILE = path.join(STATE_DIR, 'cookie-secret');
const RESET_FILE = path.join(STATE_DIR, 'visitor-left');
const CLIENT_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), 'client/dist');
const COOKIE_HOURS = 12;
const SESSION_LIMIT_MS = 20 * 60 * 1000;
const RETRY_MS = 15 * 1000; // the client retries on its own, and a retry is not a visitor leaving
const LOGIN_ATTEMPTS_PER_MINUTE = 5;
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };

const hashPassword = (password, salt) => scryptSync(password, salt, 32).toString('hex');

function setPassword() {
    const password = randomBytes(9).toString('base64url');
    const salt = randomBytes(16).toString('hex');
    mkdirSync(STATE_DIR, { recursive: true, mode: 0o700 });
    writeFileSync(PASSWORD_FILE, `${salt}:${hashPassword(password, salt)}\n`, { mode: 0o600 });
    console.log(`New password: ${password}`);
}

if (process.argv.includes('--set-password')) {
    setPassword();
    process.exit(0);
}

const [salt, passwordHash] = readFileSync(PASSWORD_FILE, 'utf8').trim().split(':');
if (!existsSync(SECRET_FILE)) {
    writeFileSync(SECRET_FILE, randomBytes(32).toString('hex'), { mode: 0o600 });
}
const secret = readFileSync(SECRET_FILE, 'utf8');
if (!existsSync(RESET_FILE)) {
    writeFileSync(RESET_FILE, '');
}

const sign = (value) => createHmac('sha256', secret).update(value).digest('hex');

function sameText(a, b) {
    const left = Buffer.from(a);
    const right = Buffer.from(b);
    return left.length === right.length && timingSafeEqual(left, right);
}

function signedIn(request) {
    const match = /(?:^|;\s*)session=(\d+)\.([0-9a-f]+)/.exec(request.headers.cookie ?? '');
    return Boolean(match) && Number(match[1]) > Date.now() && sameText(match[2], sign(match[1]));
}

function clientAddress(request) {
    return request.headers['x-forwarded-for']?.split(',')[0].trim() ?? request.socket.remoteAddress;
}

const attempts = new Map();

function loginAllowed(address) {
    const now = Date.now();
    const recent = (attempts.get(address) ?? []).filter((time) => now - time < 60000);
    recent.push(now);
    attempts.set(address, recent);
    return recent.length <= LOGIN_ATTEMPTS_PER_MINUTE;
}

const loginPage = (message) => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ventilation Digital Twin</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#14183c;color:#e6e9f5;font:16px system-ui,sans-serif}
form{display:grid;gap:12px;width:min(320px,calc(100vw - 32px))}h1{font-size:22px;margin:0 0 8px}
input,button{font:inherit;padding:10px 12px;border-radius:4px;border:1px solid #4a5280}
input{background:#1b2040;color:inherit}button{background:#1e6ebe;color:#fff;border-color:#1e6ebe;cursor:pointer}
p{margin:0;color:#ff8a75}</style></head><body><form method="post" action="/login">
<h1>Ventilation Digital Twin</h1><input type="password" name="password" placeholder="Password" aria-label="Password" autofocus required>
<button>Enter</button>${message ? `<p>${message}</p>` : ''}</form></body></html>`;

function sendLogin(response, status, message) {
    response.writeHead(status, { 'content-type': 'text/html', 'cache-control': 'no-store' });
    response.end(loginPage(message));
}

function handleLogin(request, response) {
    if (!loginAllowed(clientAddress(request))) {
        sendLogin(response, 429, 'Too many attempts. Wait a minute.');
        return;
    }
    let body = '';
    request.on('data', (chunk) => {
        body += chunk;
        if (body.length > 1024) {
            request.destroy();
        }
    });
    request.on('end', () => {
        const password = new URLSearchParams(body).get('password') ?? '';
        if (!sameText(hashPassword(password, salt), passwordHash)) {
            sendLogin(response, 401, 'Wrong password.');
            return;
        }
        const expires = String(Date.now() + COOKIE_HOURS * 3600 * 1000);
        const secure = request.headers['x-forwarded-proto'] === 'https' ? '; Secure' : '';
        response.writeHead(303, {
            location: '/',
            'set-cookie': `session=${expires}.${sign(expires)}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${COOKIE_HOURS * 3600}${secure}`,
        });
        response.end();
    });
}

function serveClient(request, response) {
    const url = new URL(request.url, 'http://localhost');
    const relative = url.pathname === '/' ? 'index.html' : path.normalize(url.pathname).slice(1);
    const file = path.join(CLIENT_DIR, relative);
    if (!file.startsWith(CLIENT_DIR + path.sep) || !existsSync(file) || !statSync(file).isFile()) {
        response.writeHead(404).end();
        return;
    }
    response.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' });
    createReadStream(file).pipe(response);
}

let visitor = null; // the visitor's signaling socket, because Kit serves one client

const server = http.createServer((request, response) => {
    const { pathname } = new URL(request.url, 'http://localhost');
    if (pathname === '/login' && request.method === 'POST') {
        handleLogin(request, response);
    } else if (!signedIn(request)) {
        sendLogin(response, 200, '');
    } else if (pathname === '/status') {
        response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
        response.end(JSON.stringify({ busy: visitor !== null }));
    } else {
        serveClient(request, response);
    }
});

function visitorLeft(arrived) {
    visitor = null;
    if (Date.now() - arrived > RETRY_MS) {
        const now = new Date();
        utimesSync(RESET_FILE, now, now);
    }
}

server.on('upgrade', (request, socket, head) => {
    if (!signedIn(request) || !request.url.startsWith('/sign_in') || visitor) {
        socket.end('HTTP/1.1 403 Forbidden\r\n\r\n');
        return;
    }
    const kit = net.connect(KIT_SIGNAL_PORT, '127.0.0.1', () => {
        const headers = Object.entries(request.headers).map(([name, value]) => `${name}: ${value}`);
        kit.write(`GET ${request.url} HTTP/1.1\r\n${headers.join('\r\n')}\r\n\r\n`);
        kit.write(head);
        socket.pipe(kit).pipe(socket);
    });
    visitor = socket;
    const arrived = Date.now();
    const limit = setTimeout(() => socket.destroy(), SESSION_LIMIT_MS);
    let closed = false;
    const close = () => {
        if (closed) {
            return;
        }
        closed = true;
        clearTimeout(limit);
        kit.destroy();
        socket.destroy();
        visitorLeft(arrived);
    };
    socket.on('close', close);
    socket.on('error', close);
    kit.on('close', close);
    kit.on('error', close);
});

server.listen(PORT, '127.0.0.1', () => console.log(`Gateway on 127.0.0.1:${PORT}`));
