# 從專案根目錄建置、共用根.dockerignore；首次內容由離線工具放入Docker volume。
FROM nginx:1.30.6-alpine
COPY public_frontend/nginx.conf /etc/nginx/nginx.conf
COPY public_frontend/nginx-https.conf /etc/nginx/transport.conf
COPY public_frontend/dist/lab.html /srv/code/index.html
COPY public_frontend/dist/lab.js public_frontend/dist/api.js public_frontend/dist/lab.css public_frontend/dist/gold.css /srv/code/
# 不依賴建置來源的權限；Nginx以非root讀取程式，volume與TLS掛載另行管理。
RUN chmod 0755 /etc/nginx /srv /srv/code \
    && chmod 0644 /etc/nginx/nginx.conf /etc/nginx/transport.conf /srv/code/*
USER 10001:10001
# 建置時就以執行UID核對讀取權限；HTTP片段／TLS等運行時掛載仍須在部署環境檢查。
RUN test -x /etc/nginx && test -x /srv/code \
    && for file in /etc/nginx/nginx.conf /etc/nginx/transport.conf /etc/nginx/mime.types /srv/code/*; do \
         test -r "$file" || exit 1; \
       done
# 不跑映像預設的設定改寫腳本；設定及根檔案系統維持唯讀。
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
