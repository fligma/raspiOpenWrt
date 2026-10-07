#!/usr/bin/env python3
import sys
import os
import urllib.parse

# Mandatory HTTP headers
sys.stdout.write("Content-Type: text/html; charset=utf-8\r\n\r\n")

# Parse GET parameters
query_string = os.environ.get("QUERY_STRING", "")
params = urllib.parse.parse_qs(query_string)
name = params.get("name", ["Guest"])[0]

# HTML & Asset linking
html = f"""<!DOCTYPE html>
<html>
<head>
    <title>rasconf test</title>
    <link rel="icon" href="/assets/favicon.ico" sizes="any">
    <link rel="apple-touch-icon" sizes="180x180" href="/assets/apple-touch-icon.png">
    <link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="16x16" href="/assets/favicon-16x16.png">
    <link rel="manifest" href="/assets/site.webmanifest">
    <link rel="stylesheet" href="/css/test.css">
</head>
<body>
    <div class="container">
        <h1>rasconf test</h1>
        <p>Hello, <b>{name}</b>!</p>
        <p>Query String: <code>{query_string}</code></p>
        <p><a href="?name=YourNameHere">Query String Test Link</a></p>
        <button id="testBtn">Click Me</button>
        <img src="/assets/250px-BrownSpiderMonkey_(edit2).jpg">
    </div>
    <script src="/js/test.js"></script>
</body>
</html>
"""
sys.stdout.write(html)