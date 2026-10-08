# Palo Alto, private ELB và kết nối CMC

Cấu hình cần thực hiện trên Portal/firewall và các giá trị cần đưa vào GitOps. Tài liệu này không xác nhận tài nguyên cloud đã được tạo hoặc kiểm thử. Nguồn: tong-hop-ha-tang-metasfresh-cmc-cloud-20261007.docx; cập nhật PostgreSQL RDS và Harbor do người dùng cung cấp ngày 08/10/2026 được ưu tiên hơn danh mục ngày 07/10. Không ghi credential, private key hoặc kubeconfig vào Git.

## Địa chỉ và interface

| Thành phần | Địa chỉ | Vai trò |
|---|---|---|
| pfSense WAN | 10.60.10.78; EIP 203.205.45.207 | OpenVPN UDP1194; điểm vào ERP nếu dùng chung EIP |
| pfSense LAN / VPN pool | 10.60.50.78 / 10.61.60.0/24 | Giá trị thực tế; thay dải VPN mẫu 10.61.50.0/24 |
| Bastion ADMIN / MGMT | 10.60.50.20 / 10.60.60.20 | Nguồn kết nối phụ thuộc NIC/route |
| PA UNTRUST VIP | 10.60.10.100/24 | PA-01 ethernet1/1; fixed IP cloud PA-01/02 .11/.12 |
| PA TRUST VIP | 10.60.20.100/24 | PA-01 **ethernet1/2**; fixed IP cloud PA-01/02 .11/.12 |
| PA ADMIN VIP | 10.60.50.100/24 | PA-01 ethernet1/5 |
| PA management | 10.60.60.11 / .12 | Đường quản trị riêng |
| CaaS API | https://10.60.30.62:6443 | kubernetes-final-lab |
| APP workers | 10.60.30.140 / .240 / .80 | Nodegroup nodegroup-gd6u |
| MON / PLATFORM workers | 10.60.65.115 / 10.60.66.135 | Không làm backend Ingress |
| Managed RDS PostgreSQL 18 | write 10.60.40.113; read .126 / .45 | rds_postgres-erp; MasterSlave; cập nhật 08/10 |
| Rabbit / Search | 10.60.40.104:5672 / 10.60.40.30:9200 | DATA |
| Harbor / Runner dự kiến | 10.60.50.30:443 | HTTPS IP, CA nội bộ; kiểm chứng runner đã đăng ký |
| ecs-mgmt | 10.60.60.227 / 10.60.20.170 | Hiện chưa dùng; không thêm vào luồng ERP |

Theo ảnh ngày 07/10, TRUST là ethernet1/2, khác tên ethernet1/1 trong yêu cầu. Đối chiếu MAC cloud với PAN-OS trước khi thao tác; mapping PA-02 chưa ghi nhận đầy đủ. 10.60.10.101/32 đang tồn tại nhưng chưa xác định mục đích. AAP TRUST có thêm 10.60.10.1/32; xác minh với CMC trước khi thay đổi.

ELB phải có **VIP riêng trong TRUST 10.60.20.0/24**, không dùng .100 vì đó là IP của Palo. Tên subnet không thay thế UUID trong API/annotation.

## Điểm vào Internet

Phương án dùng EIP hiện có:

Internet/Cloudflare -> 203.205.45.207 -> pfSense WAN .10.78 -> PA UNTRUST VIP .10.100:443 -> PA TRUST VIP .20.100 -> private ELB TCP443 -> APP NodePort31443 -> HAProxy Ingress -> UI/API.

OpenVPN giữ UDP1194 riêng. Trước thao tác NAT, giữ đường quản trị VPN/Bastion và console CMC hoạt động.

| Cấu hình pfSense | Giá trị |
|---|---|
| WAN SG và firewall | TCP443 từ Cloudflare CIDR mới nhất hoặc nguồn kiểm thử /32 được phép |
| NAT Port Forward | WAN; TCP; destination WAN address:443; redirect 10.60.10.100:443; associated filter rule |
| Hybrid Outbound NAT | WAN; source đúng nguồn của rule publish; destination 10.60.10.100/32 TCP443; translation WAN address 10.60.10.78 |

SNAT pfSense chỉ áp dụng luồng publish này để PA trả về .10.78 và giữ phiên NAT. Chỉ DNAT có thể làm PA trả qua gateway CMC 10.60.10.1, bỏ qua pfSense. Không đổi default route PA/Bastion để chữa riêng luồng này. Kiểm tra NAT state và packet capture cho đường WAN -> WAN trên CMC. [Netgate Port Forwarding](https://docs.netgate.com/pfsense/en/latest/nat/port-forwards.html), [Outbound NAT](https://docs.netgate.com/pfsense/en/latest/nat/outbound.html).

WAN443 có thể trùng GUI pfSense; quản trị GUI qua LAN/VPN, không publish GUI WAN. Một pfSense là điểm lỗi đơn của đường ERP này; HA Palo không loại bỏ điểm lỗi đó.

Phương án EIP ERP riêng đưa traffic trực tiếp tới PA UNTRUST VIP .10.100. Phương án này chưa được ghi nhận triển khai; CMC phải xác nhận EIP/VIP/AAP và failover. Khi chọn phương án này, bỏ chuyển ERP qua pfSense và giữ pfSense cho VPN. Không gắn EIP vào worker hoặc private ELB.

Domain ERP có thể proxy Cloudflare; domain VPN phải DNS-only. Cập nhật nguồn tại điểm vào theo [Cloudflare IP ranges](https://www.cloudflare.com/ips/), dùng TLS origin hợp lệ và Full (strict). Sau SNAT, IP socket trong Ingress không tự phản ánh client. Chỉ tin header client-IP của Cloudflare khi tất cả đường vào origin đều bị giới hạn đúng Cloudflare; không tin header do client truy cập trực tiếp tự gửi. Không bật PROXY protocol chỉ ở một đầu.

## Rule PA kết hợp DNAT và SNAT

Sau khi có VIP thật, tạo address objects ERP-UNTRUST-VIP=10.60.10.100/32 và ERP-PRIVATE-ELB=VIP_ELB/32. Đặt rule cụ thể trước NAT tổng quát; không tách DNAT/SNAT thành hai rule với kỳ vọng cùng phiên áp dụng cả hai.

| Trường NAT | Giá trị |
|---|---|
| Original source zone | UNTRUST |
| Original destination zone | UNTRUST theo route IP pre-NAT .10.100 |
| Original source | .10.78/32 ở shared-EIP; Cloudflare CIDR ở direct-EIP |
| Original destination | ERP-UNTRUST-VIP |
| Service | TCP443 |
| Destination translation | Static IP VIP_ELB, translated port443 |
| Source translation | Dynamic IP and Port; TRUST interface đã đối chiếu; IP 10.60.20.100 |

Security policy: UNTRUST -> TRUST, source như trên, **destination address vẫn ERP-UNTRUST-VIP pre-NAT**, TCP443, allow, log session end và security profiles phù hợp giấy phép. Security policy dùng địa chỉ original và zone sau NAT. [Palo Alto NAT Policy Rules](https://docs.paloaltonetworks.com/ngfw/networking/nat/nat-policy-rules).

SNAT .20.100 giúp ELB trả về PA cùng TRUST subnet, không cần đổi gateway ELB. Giữ default route PA qua UNTRUST gateway .10.1; kiểm tra connected TRUST route/virtual router. AAP .10.100 và .20.100 cần đúng trên cả hai PA; CMC phải xác nhận anti-spoofing, MAC và failover.

## Service tạo private ELB

Service ingress-system/haproxy-ingress: LoadBalancer; chỉ TCP443; HTTPS NodePort31443; externalTrafficPolicy Local; healthCheckNodePort32042. TLS terminate tại HAProxy Ingress; không chọn Terminated HTTPS tại ELB cho thiết kế này.

CMC công bố các annotation:

    loadbalancer.openstack.org/class: cmc-loadbalancer-private
    loadbalancer.openstack.org/flavor-id: a80cfbb4-326b-4819-8e67-58eca8495490

Flavor là small HCM; kiểm tra region/quota. [CMC Service LoadBalancer](https://cmccloud.vn/document/Kubernetes/huong-dan/tao-service-cho-cum-k8s-voi-dich-vu-elastic-load-balancer).

Upstream OCCM có subnet-id (TRUST VIP UUID), member-subnet-id (APP backend UUID), node-selector=cmc-cloud-k8s-nodegroups=nodegroup-gd6u và openstack-internal-load-balancer=true. Class configuration có thể ưu tiên hơn subnet annotations. Hỗ trợ upstream không chứng minh phiên bản controller CMC hỗ trợ toàn bộ; phải kiểm tra VIP/backend thực tế. [OCCM annotations](https://github.com/kubernetes/cloud-provider-openstack/blob/master/docs/openstack-cloud-controller-manager/expose-applications-using-loadbalancer-type-service.md).

Đặt loadBalancerSourceRanges=[10.60.20.100/32], nhưng upstream chỉ xác nhận thực thi với Octavia API>=v2.12. Kiểm tra ELB private, VIP thuộc TRUST, listener allowed CIDRs chỉ .20.100/32, backend APP, không có EIP; thử nguồn trái phép phải thất bại. Nếu class không tạo VIP tại TRUST, dừng publish để sửa provider/config.

Fallback: ELB private thủ công/CMC Terraform, TRUST subnet, TCP443 -> APP .140/.240/.80:31443, health TCP31443, allowed CIDRs .20.100/32; Service chuyển NodePort để tránh tạo thêm ELB. Không đặt healthCheckNodePort cho Service thuần NodePort. Với Local và hai Ingress replica, node không có pod local Ready có thể không healthy. [CMC Pool](https://cmccloud.vn/document/elastic-load-balancer/huong-dan/khoi-tao-pool-tao-quy-tac-load-balancer), [CMC Terraform ELB](https://cmccloud.vn/document/cloud-terraform/huong-dan/Provision-dich-vu-Load-Balancer-voi-Terraform).

## SG, DATA và điều kiện bootstrap

ELB nhận TCP443 chỉ từ PA .20.100. APP nhận TCP31443 và health32042 từ **nguồn backend/health thực tế của ELB**, không mặc định từ PA hoặc ELB VIP. Ghi nhận Portal/capture rồi chốt CIDR.

SG mặc định CaaS ngày07/10 gắn cả năm worker đang allow 0.0.0.0/0 cho SSH, kubelet, etcd, CNI và TCP/UDP30000-32767. Thêm SG hẹp không phủ định allow này: SG được hợp quyền. CMC xác nhận replacement/remote scope trước thu hẹp; không xóa mù SG hệ thống/CNI. [CMC SG stateful và hợp quyền](https://cmccloud.vn/document/security-group/cau-hoi-thuong-gap/cau-hoi-thuong-gap).

RDS18 là dịch vụ managed, không phải ba VM PostgreSQL tự quản. ERP dùng write .40.113; read .126/.45 chưa được đưa vào JDBC URL hoặc read routing. Replication/failover do CMC quản lý; không tạo manual peer replication SG, không mở SSH22 hay exporter9100/9187 trên RDS. Cấp quyền client qua cơ chế access control của RDS/CMC. pg_exporter chạy trong MON và truy vấn RDS5432 bằng role monitoring riêng. Runner dump chỉ tới write endpoint; RDS admin/SUPERUSER không dùng làm runtime ERP.

SG pg trong ảnh07/10 có RDP3389 từ mọi nguồn và chưa có5432; đây là trạng thái lịch sử trước thông tin RDS08/10. Kiểm tra SG attachment hiện tại; bỏ RDP nếu không dùng. Không tự gắn SG VM cũ vào managed RDS.

Source code có DB image PostgreSQL15.4 và baseline metasfresh5.175, **chưa chứng minh tương thích RDS18**. Cần chạy schema migration, JDBC, chức năng nghiệp vụ và pg_dump/restore với phiên bản client hỗ trợ server18; không bypass version mismatch. Read replicas không tự làm ERP đọc/ghi đúng hoặc cung cấp failover được kiểm chứng.

Rabbit management bind127.0.0.1:15672, quản trị qua SSH tunnel. Search chỉ publish9200; không mở9300 mặc định. Harbor HTTPS .50.30 dùng CA nội bộ; cài CA vào runtime containerd của cả năm node, không chỉ Docker Runner. imagePullSecret không sửa lỗi CA.

GitOps repo riêng: https://github.com/hoi3dkhoiche-byte/GitOps-final-lab.git. Các input còn phải điền: TRUST/APP UUID, private ELB VIP, ERP domain/cert, StorageClass, S3 endpoint/region/bucket/CA, provider controller/Octavia version, nguồn ELB backend/health, RDS access control/TLS/user/schema và credential thật. Chỉ public endpoint/sealed manifest được commit.

Cluster được ghi v1.35.3, Calico, PodCIDR10.100/16, ServiceCIDR10.254/16. PLATFORM/MON có uninitialized=true:NoSchedule; kiểm tra Ready/CCM/ProviderID. Không xóa taint hoặc thêm toleration để che lỗi khởi tạo.

Từ Bastion, kubeconfig riêng ngoài Git:

    kubectl get nodes -o wide
    kubectl get nodes -L cmc-cloud-k8s-nodegroups
    kubectl get storageclass
    kubectl -n ingress-system get svc haproxy-ingress -o yaml
    kubectl -n ingress-system get pods -o wide

Dùng ip route get với IP đích trên Bastion để kiểm tra source NIC. Đo pod->DATA là node SNAT hay podIP10.100/16 trước chốt SG. NetworkPolicy chỉ cho Core/API tới RDSwrite5432, Rabbit5672, Search9200. Browser gọi API/WebSocket qua Ingress; UI tĩnh không cần gọi API từ pod.

Nghiệm thu image pull/digest/CA; podReady; TLS/SNI; đăng nhập/tạo đơn/WebSocket; ELB health; nguồn trái phép không truy cập ELB/NodePort; log PF/PA/Ingress; restore PG/PVC; failover PA đo thật. HA2 sync Enabled/AAP chưa đủ chứng minh HA. HA1 cleartext TCP28260/28769 và ICMP chỉ peer .71.11/.12; nếu encryption bật, kiểm tra bộ port đúng. HA2 hiện UDP29281 chỉ peer .72.11/.12. [Palo Alto HA ports](https://docs.paloaltonetworks.com/ngfw/administration/firewall-administration/reference-port-number-usage/ports-used-for-ha).
