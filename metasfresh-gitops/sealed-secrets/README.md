# Hướng dẫn tạo Sealed Secrets

Theo kế hoạch triển khai (SCT01, SCT02), các secret không bao giờ được lưu plain-text vào Git. Nhóm phải sử dụng `kubeseal` để mã hóa Secret trước khi commit.

## 1. Các Secret cần tạo
1. `erp-db-secret`: Chứa DB password cho Core, API.
2. `erp-properties`: File `metasfresh.properties` hoàn chỉnh.
3. `erp-rabbit-secret`: Thông tin đăng nhập RabbitMQ.
4. `harbor-pull`: Docker registry credentials (cho namespace `erp` và `monitoring`).
5. `erp-origin-tls`: Chứa TLS Origin Certificate từ Cloudflare.

## 2. Cách tạo một SealedSecret
Bước 1: Tạo secret dạng yaml nhưng KHÔNG commit.
```bash
kubectl create secret generic erp-db-secret \
  --namespace erp \
  --from-literal=DB_USER=metasfresh \
  --from-literal=DB_PASSWORD=SuperSecretPassword123 \
  --dry-run=client -o yaml > temp-secret.yaml
```

Bước 2: Dùng kubeseal mã hóa (sử dụng public cert đã tải từ controller).
```bash
kubeseal --format=yaml --cert=pub-cert.pem < temp-secret.yaml > erp-db-sealed.yaml
```

Bước 3: Xóa file temp và commit file `erp-db-sealed.yaml` vào thư mục này.
```bash
rm temp-secret.yaml
git add erp-db-sealed.yaml
git commit -m "chore: add db sealed secret"
```
