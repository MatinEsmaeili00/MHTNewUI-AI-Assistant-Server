# Network: the app in Los Angeles, the Spark in Louisiana

The app only makes outgoing HTTP requests to the server (`GET /health`, `POST /v1/chat`, `POST /v1/voice`). The
server never connects back. So the only question is: how does a laptop or iPad on some network in Los Angeles
reach port 8010 of a machine inside the university network in Louisiana?

The Spark's campus address (the earlier robot project used `130.39.94.33`) is normally not reachable from
outside the campus. Three ways around that, best first.

## A. Tailscale (recommended)

A private network between your own devices. Nothing is opened on the campus firewall, the traffic is encrypted,
and the Spark keeps one name wherever the laptop is.

On the Spark (needs you for the login link):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # prints a link: open it, sign in
tailscale ip -4              # the Spark's address on the private network, 100.x.y.z
tailscale status             # its name, e.g. "spark"
```

On the Mac (and the iPad): install Tailscale from tailscale.com or the App Store, sign in with the same account.

In the app's chat box:

```
/server http://100.x.y.z:8010        (or http://spark:8010 with MagicDNS on)
/key <the key from the Spark's .env>
/status
```

Notes:

- Leave the server listening on `0.0.0.0:8010` (what `run.sh` does) and keep the key on. If the Spark has a
  firewall (`sudo ufw status`), allow the port on the Tailscale interface only:
  `sudo ufw allow in on tailscale0 to any port 8010 proto tcp`.
- Some networks block the direct path; Tailscale then relays the traffic. It still works, a little slower.
  `tailscale ping spark` on the Mac shows which one you got.
- Keep the Spark from sleeping and Tailscale starting at boot (`sudo systemctl enable --now tailscaled`, on by
  default after the install).
- On an iPad build the address must be `https://`: see "iPad" below.

## B. A tunnel (when nothing can be installed on the client)

`cloudflared tunnel --url http://localhost:8010` on the Spark prints a public `https://...trycloudflare.com`
address that forwards to the server. Anyone with the address reaches the server, so the key
(`ASSISTANT_API_KEY`) is not optional here. The free address changes every time the tunnel restarts; a fixed
one needs a Cloudflare account and a domain.

## C. The university VPN

If the university's VPN puts the laptop on a network that can reach the lab machines, the campus address works
as it does in the lab: `/server http://<campus address>:8010`. Whether the VPN allows that is a question for
campus IT; test it from outside the campus before travelling.

## Not recommended

Asking for port 8010 to be opened to the internet on the campus firewall. The server has a key, but it was
written for a private link, not for the open internet.

## The key

Always run the server with a key when it is reachable from another machine:

```bash
echo "ASSISTANT_API_KEY=$(openssl rand -hex 24)" >> .env
```

The app sends it as `Authorization: Bearer <key>`. Set it once in the app with `/key <text>`; it is remembered
on that computer (in the app's `GameUserSettings.ini`). `/health` needs no key, so `/status` in the app tells
you "the server answers" even with a wrong key; a wrong key shows up at the first real request ("The AI server
did not accept my key").

## iPad

Not tested. Two things are known in advance:

- iOS refuses plain `http://` from apps unless the app declares an exception. With Tailscale, the clean way is
  an `https://` address: `tailscale serve --bg 8010` on the Spark gives `https://<name>.<tailnet>.ts.net`
  with a real certificate, reachable only from your own devices.
- The microphone needs a usage description in the app's Info.plist (`NSMicrophoneUsageDescription`) or iOS
  closes the app at the first recording.

## Checking the link

From the Mac, before starting the app:

```bash
curl http://<address>:8010/health
curl -X POST http://<address>:8010/v1/chat -H "Authorization: Bearer <key>" \
     -H "Content-Type: application/json" -d '{"protocol":1,"session_id":"t","text":"pause","context":{},"catalog_id":""}'
```

What to expect for speed: a typed command answered by the rules is one small request and one small answer;
the round trip is the network delay between the two cities plus a few milliseconds. A spoken command uploads
about 100 kB per second of speech (16-bit mono at 48 kHz) before speech recognition starts.
