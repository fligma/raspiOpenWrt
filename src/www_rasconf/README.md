#setup
apk add python3
uci set uhttpd.rasconf=uhttpd
uci add_list uhttpd.rasconf.listen_http='0.0.0.0:8989'
uci set uhttpd.rasconf.home='/www_rasconf'
uci set uhttpd.rasconf.cgi_prefix='/cgi-bin'
uci commit uhttpd
/etc/init.d/uhttpd restart

#pages
http://192.168.50.1 Luci
http://192.168.50.1:8989/cgi-bin/index.py dashboard
http://192.168.50.1:8989/cgi-bin/index.py?action=api&type=sys api calls
http://192.168.50.1:8989/cgi-bin/index.py?action=api for all