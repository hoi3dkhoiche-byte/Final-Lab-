#!/bin/bash
set -e

echo "=== 1. Cài đặt Argo CD vào namespace argocd ==="
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/v2.12.3/manifests/install.yaml

echo "Chờ Argo CD khởi động (có thể mất vài phút)..."
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=argocd-server -n argocd --timeout=300s

echo "=== 2. Cài đặt Sealed Secrets Controller ==="
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.27.1/controller.yaml

echo "=== 3. Cập nhật Repo URL cho ERP Application ==="
# Thay thế placeholder bằng repo thực tế
read -p "Nhập URL Git Repo của bạn (ví dụ: https://github.com/hoi3dkhoiche-byte/Final-Lab-.git): " REPO_URL
if [ -n "$REPO_URL" ]; then
  sed -i "s|repoURL: 'YOUR_GIT_REPO_URL'|repoURL: '$REPO_URL'|g" platform/argocd/application-erp.yaml
fi

echo "=== 4. Khởi tạo GitOps AppProject và Application ==="
kubectl apply -f platform/argocd/app-project-erp.yaml
kubectl apply -f platform/argocd/application-erp.yaml

echo "=== 5. Hoàn tất ==="
echo "Argo CD đã được cài đặt và cấu hình ứng dụng metasfresh-erp."
echo "Để lấy mật khẩu đăng nhập Argo CD (admin):"
echo "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo"
echo ""
echo "Để truy cập UI của Argo CD ở local:"
echo "  kubectl port-forward svc/argocd-server -n argocd 8080:443"
echo "Truy cập: https://localhost:8080"
