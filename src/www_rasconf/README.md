# setup
```apk add python3
uci set uhttpd.rasconf=uhttpd
uci add_list uhttpd.rasconf.listen_http='0.0.0.0:8989'
uci set uhttpd.rasconf.home='/www_rasconf'
uci set uhttpd.rasconf.cgi_prefix='/cgi-bin'
uci commit uhttpd
/etc/init.d/uhttpd restart
```

# Credentials
Generate new hash via CLI: ```python -c "import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())"```
Then Place it in file `/www_rasconf/cgi-bin/hash.file`
`pass123` DEFAULT PASSWORD

# rasconf web ui pages
http://0.0.0.0:8989/cgi-bin/index.py dashboard
http://0.0.0.0:8989/cgi-bin/index.py?action=api&type=sys api calls
http://0.0.0.0:8989/cgi-bin/index.py?action=api for all

# layout
`cgi-bin/index.py` is the CGI entry point. It imports these sibling modules, so
deploy the whole `cgi-bin` folder (not just `index.py`):
- `auth.py` - PIN hash file handling and session auth
- `store.py` - refresh-interval config load/save (`/config/index.conf`)
- `sysinfo.py` - temperature, load/memory, per-core CPU, storage, traffic
- `netinfo.py` - interfaces, wireless, connected devices, Wi-Fi deauth
- `pages.py` - login and dashboard HTML templates

`hash.file` is created automatically in `cgi-bin` on first run if missing.
