# Berry web dashboard

The web UI that runs on the Raspberry Pi itself: plain Python CGI served by
uhttpd, no web framework and nothing else to install beyond `python3`. The
**Berry web dashboard** addon in the addon store (`berry_web`) puts this folder
onto a device from GitHub and sets the web server up for it, or copy it here
yourself - it belongs in `/www_berry`.

## Setup

The addon does all of this; these are the commands it runs, so a device can be
done by hand instead. The section is named `berry`, and an older install that
used `uhttpd.rasconf` keeps working under that name until the folder itself is
moved.

```sh
apk add python3
uci set uhttpd.berry=uhttpd
uci add_list uhttpd.berry.listen_http='0.0.0.0:8989'
uci set uhttpd.berry.home='/www_berry'
uci set uhttpd.berry.cgi_prefix='/cgi-bin'
uci commit uhttpd
/etc/init.d/uhttpd restart
```

## Credentials

The dashboard asks for a PIN first. The PIN is stored as a SHA-256 hash in
`/www_berry/cgi-bin/hash.file`, generated on the device with:

```sh
python -c "import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())"
```

The hash file is created automatically on first run with `pass123` as the
PIN - change it before the device is reachable from anywhere but your LAN.
It belongs to the device and is not part of this folder: the addon keeps it out
of every upload and every tidy-up.

## Pages and API

- `http://<device>:8989/cgi-bin/index.py` - the dashboard
- `http://<device>:8989/cgi-bin/index.py?action=api` - every reading as JSON
- `...?action=api&type=sys` - just the system part (temperature, load,
  memory, storage, traffic)

The JSON endpoints are what Berry Manager and any script on the LAN can use;
the HTML pages are what a browser gets.

## Layout

`cgi-bin/index.py` is the CGI entry point. It imports these sibling modules,
so deploy the whole `cgi-bin` folder (not just `index.py`):

- `auth.py` - PIN hash file handling and session auth
- `store.py` - refresh-interval config load/save (`/config/index.conf`)
- `sysinfo.py` - temperature, load/memory, per-core CPU, storage, traffic
- `netinfo.py` - interfaces, wireless, connected devices, Wi-Fi deauth
- `pages.py` - login and dashboard HTML templates

`css/`, `js/` and `assets/` hold the page's own styling, scripts and icons;
the HTML references them by path, so they travel with `cgi-bin`.

`css/theme.css` is not written by hand: it holds the colours of the Berry
Manager theme that installed the page, and an install overwrites it. The
committed copy carries the program's default theme, so a folder copied across
by hand still looks like the app.
