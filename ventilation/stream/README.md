# Public stream

Runs the garage in a browser, behind a password.

```
Browser --HTTPS--> nginx --> gateway.mjs --> Kit signaling on 127.0.0.1:49100
Browser <--video over UDP 47998-- Kit
```

- `gateway.mjs` checks the password, serves the web client, and carries Kit's
  signaling connection. Kit's port 49100 stays closed to the internet.
- `client/` is the web page, built with `npm ci && npm run build`.
- `run-kit.sh` starts the streaming app with the server's public address.
- `setup-public.sh` does the one-time setup: address, firewall, HTTPS, services.

Kit serves one visitor at a time. The gateway ends a session after 20 minutes,
the page ends it after 5 idle minutes, and the app reloads the scene afterwards.

## Everyday commands

```bash
sudo systemctl status ventilation-kit ventilation-gateway
sudo systemctl restart ventilation-kit            # after changing the app
journalctl -u ventilation-kit -f                  # live log
sudo systemctl stop ventilation-kit               # free the GPU for the desktop app
~/.local/opt/node-v22.11.0-linux-x64/bin/node ventilation/stream/gateway.mjs --set-password
sudo systemctl restart ventilation-gateway        # after changing the password
```
