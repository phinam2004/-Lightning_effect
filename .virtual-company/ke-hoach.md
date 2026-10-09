# 📐 BẢN VẼ KIẾN TRÚC & KẾ HOẠCH TRIỂN KHAI (PLANNER)

## 🎯 MỤC TIÊU
Khắc phục lỗi kích thước file vượt quá giới hạn GitHub (100MB do `.venv/Lib/site-packages/cv2/cv2.pyd`) và đẩy code dự án lên kho lưu trữ GitHub `phinam2004/-Lightning_effect`.

## 🔍 NGUYÊN NHÂN LỖI
1. Commit cục bộ gần nhất (`7a33671`) đã commit toàn bộ thư mục môi trường ảo `.venv/` (hơn 5600 files, trong đó có `cv2.pyd` dung lượng 107.67MB) và cache `__pycache__/`.
2. Dự án chưa có file `.gitignore` chuẩn (chỉ có file `env.gitignore` không có tác dụng bỏ qua `.venv`).
3. GitHub từ chối lệnh `git push` nếu trong commit history chứa bất kỳ file nào lớn hơn 100MB.

## 📋 CÁC BƯỚC THỰC HIỆN
1. **Giai đoạn 1: Khôi phục commit cục bộ và bảo vệ mã nguồn**
   - Chạy `git reset HEAD~1` để hoàn tác commit chứa `.venv` nhưng giữ nguyên toàn bộ file mã nguồn trong working directory.
2. **Giai đoạn 2: Chuẩn hóa `.gitignore`**
   - Tạo file `.gitignore` chuẩn cho Python: loại trừ `.venv/`, `venv/`, `__pycache__/`, `.env`, build artifacts...
   - Xóa file `env.gitignore` không chuẩn.
3. **Giai đoạn 3: Commit sạch và Push lên GitHub**
   - Stage toàn bộ mã nguồn hợp lệ (code, config, model `hand_landmarker.task`, `DESIGN.md`, `.virtual-company/`).
   - Tạo commit mới sạch sẽ, không chứa binary nặng của thư viện môi trường ảo.
   - Thực hiện `git push origin main` đẩy toàn bộ lên GitHub.
4. **Giai đoạn 4: QA & Review**
   - Tester kiểm tra trạng thái remote, branch, xác nhận GitHub đã nhận commit thành công.
   - Reviewer đối chiếu `git status` và `git log` để phê duyệt.
