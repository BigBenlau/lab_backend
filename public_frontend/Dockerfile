# 只建置頁面程式；首次內容由離線工具放入 Docker volume。
FROM nginx:1.30.6-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY nginx-https.conf /etc/nginx/transport.conf
COPY dist/lab.html /srv/code/index.html
COPY dist/lab.js dist/api.js dist/lab.css dist/gold.css /srv/code/
USER 10001:10001
# 不跑映像預設的設定改寫腳本；設定及根檔案系統維持唯讀。
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
