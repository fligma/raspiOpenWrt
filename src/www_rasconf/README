apk add python3
uci set uhttpd.rasconf=uhttpd
uci add_list uhttpd.rasconf.listen_http='0.0.0.0:8989'
uci set uhttpd.rasconf.home='/www_rasconf'
uci set uhttpd.rasconf.cgi_prefix='/cgi-bin'
uci commit uhttpd
/etc/init.d/uhttpd restart
