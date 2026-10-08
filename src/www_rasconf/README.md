#setup
apk add python3
uci set uhttpd.rasconf=uhttpd
uci add_list uhttpd.rasconf.listen_http='0.0.0.0:8989'
uci set uhttpd.rasconf.home='/www_rasconf'
uci set uhttpd.rasconf.cgi_prefix='/cgi-bin'
uci commit uhttpd
/etc/init.d/uhttpd restart

# Generate new hash via CLI: python -c "import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())"
# Then Place it in file /www_rasconf/cgi-bin/hash.file

#pages
http://0.0.0.0 Luci
http://0.0.0.0:8989/cgi-bin/index.py dashboard
http://0.0.0.0:8989/cgi-bin/index.py?action=api&type=sys api calls
http://0.0.0.0:8989/cgi-bin/index.py?action=api for all
