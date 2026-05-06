#!/bin/sh
set -eu

# Keep the shared frontend_build volume in sync with the latest image build.
rm -rf /usr/share/nginx/html/*
cp -a /opt/frontend-dist/. /usr/share/nginx/html/
