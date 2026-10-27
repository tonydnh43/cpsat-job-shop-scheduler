# CP-SAT Job Shop Scheduler

Lập lịch sản xuất cho bài toán **flexible job-shop**: mỗi job đi qua các công đoạn theo thứ tự, mỗi công đoạn chọn một trong nhiều máy thay thế với thời gian xử lý khác nhau. Solver dùng OR-Tools CP-SAT, xuất JSON/CSV và Gantt HTML.

## Bài toán

Cần quyết định đồng thời: **công đoạn nào, trên máy nào, vào thời điểm nào**.

| Ràng buộc | Nội dung |
| --- | --- |
| Precedence | Công đoạn sau chỉ bắt đầu khi công đoạn trước kết thúc |
| Một máy mỗi công đoạn | Chọn đúng một máy trong `durations`, không chia nhỏ công đoạn |
| No-overlap | Một máy không xử lý hai công đoạn cùng lúc |
| Break time | Được đi qua nhưng kéo dài duration và vẫn giữ máy; không bắt đầu/kết thúc trong vùng nghỉ bị cấm |
| Downtime | Không được chạy xuyên qua khoảng máy bị chặn |
| Horizon | Mọi công đoạn kết thúc trong `horizon` |
| Deadline 70% | Đến `operations[].deadline` phải xử lý thực được ≥ 70% processing time của máy đã chọn; đây là ràng buộc cứng |

```text
processing_time = ceil(unit_duration × quantity)   # hoặc ceil(machine_duration)
duration        = processing_time + giờ nghỉ bị đi qua
```

Công thức deadline: `10 × W ≥ 7 × P`, với `P` là processing time trên máy đã chọn và `W` là số phút làm việc thực trước deadline. Giờ nghỉ không tạo tiến độ.

**Mục tiêu** (mặc định): `100 × makespan + 10 × idle máy + 10 × cân bằng tải + 10 × thời gian chờ + 0 × MaxEnd theo operation–máy`. Deadline 70% nằm ở ràng buộc, không phải điểm phạt.

## Cách giải

Job được chia thành batch để giữ bài toán trong giới hạn thời gian. Chọn chế độ ở đầu [run_scheduling.py](run_scheduling.py):

| Chế độ | Cách xếp |
| --- | --- |
| `vertical` | Xếp toàn bộ công đoạn của từng nhóm job |
| `horizontal` | Xếp OP1 của mọi job trước, rồi OP2…; sau đó dồn lịch |
| `hybrid` (mặc định) | Seed bằng horizontal, sửa từng nhóm job trên toàn route (đổi được máy, thứ tự), cuối cùng dồn toàn bộ chuỗi về sớm |

Lịch của batch trước được giữ cố định, nên không có bảo đảm tối ưu toàn cục. Khi một batch không có nghiệm, batch size giảm một nửa và thử lại. Job thất bại được ghi vào `infeasible_jobs` (`INFEASIBLE`) hoặc `unscheduled_jobs` (`UNKNOWN`, thường do hết thời gian), phần còn lại vẫn được xếp.

## Cài đặt và chạy

Cần Python ≥ 3.12 và [uv](https://docs.astral.sh/uv/), chạy từ thư mục gốc repo:

```bash
uv sync --locked
uv run --locked python run_scheduling.py
```

Runner đọc `data/algorithm_input/exp_30_jobs_schedule.json`. Dùng dữ liệu khác bằng cách sửa đường dẫn trong runner hoặc gọi `run_scheduling(input_data=...)`.

Sinh lại dataset 30 job từ mẫu `new_exp.json`:

```bash
uv run --locked python -m src.utils.dump_data
```

Lệnh này ghi `data/algorithm_input/dump_generated_30_jobs.json` và **không** tự đổi input của runner.

## Định dạng đầu vào

Thời gian là số nguyên **phút** tính từ `period_start`; `period_start` chỉ dùng để đổi sang ngày giờ khi xuất CSV/Gantt.

```json
{
  "horizon": 60,
  "period_start": "2026-10-05 08:00:00",
  "machines": {
    "OP1-MC1": {"operation_id": "OP1", "downtime": [[10, 20]]},
    "OP2-MC1": {"operation_id": "OP2", "downtime": []}
  },
  "break_times": {"OP1": [[2, 5]], "OP2": []},
  "jobs": [
    {
      "job_code": "DEMO-001",
      "job_name": "Lô demo",
      "parent_code": "PRODUCT-001",
      "quantity": 3,
      "deadline": 20,
      "operations": [
        {"operation_id": "OP1", "deadline": 20,
         "durations": {"OP1-MC1": {"unit_duration": 1.5}}},
        {"operation_id": "OP2", "deadline": 20,
         "durations": {"OP2-MC1": {"unit_duration": 2}}}
      ]
    }
  ]
}
```

| Trường | Ý nghĩa |
| --- | --- |
| `horizon` | Giới hạn kết thúc lịch (số nguyên dương) |
| `period_start` | Mốc quy đổi phút sang ngày giờ, dạng `YYYY-MM-DD HH:MM:SS` |
| `machines.*.downtime` | Các khoảng máy bị chặn |
| `break_times` | Giờ nghỉ theo `operation_id` |
| `jobs[].quantity` | Số lượng, dùng khi có `unit_duration` |
| `jobs[].deadline` | Metadata, không áp dụng cho solver |
| `operations[].deadline` | Mốc kiểm tra 70%; bỏ trống hoặc `null` để không áp dụng |
| `operations[].durations` | Máy thay thế → `unit_duration` hoặc `machine_duration` |

`job_code` phải duy nhất, `operation_id` không trùng trong cùng job, và mọi máy trong `durations` phải tồn tại trong `machines`. Thứ tự `jobs` ảnh hưởng đến cách chia batch.

## Kết quả

| File | Nội dung |
| --- | --- |
| `data/algorithm_output/YYMMDD/HHMM_schedule.json` | Input đã chuẩn hóa, lịch và trạng thái |
| `data/csv_output/YYMMDD/HHMM_schedule.csv` | Bảng công đoạn, downtime, giờ nghỉ |
| `templates/YYMMDD/HHMM_schedule.html` | Gantt tương tác theo máy |
| `logs/CPSAT_Job_Shop_Scheduler.log` | Log tìm nghiệm và lỗi |

Tên file trùng phút được thêm hậu tố `_2`, `_3`… để không ghi đè kết quả cũ.

`status: "success"` nghĩa là mọi job đều có lịch (không có nghĩa tối ưu); `status: "partial"` nghĩa là còn job trong `infeasible_jobs` hoặc `unscheduled_jobs`. `total_jobs` là số job **đã xếp**. Trong CSV, các hàng có `job_code` là `downtime` hoặc `break_time` không phải job sản xuất.

## Cấu hình

Các tham số chính trong [config.py](config.py):

| Tham số | Mặc định | Tác dụng |
| --- | --- | --- |
| `MINUTES_TIMEOUT` | `3` | Giới hạn mỗi lần gọi solver |
| `NUM_WORKERS` | `os.cpu_count()` | Số worker tìm kiếm |
| `SCHEDULING_BATCH_SIZE` | `10` | Batch size mặc định của runner |
| `SCHEDULING_MAKESPAN_WEIGHT` | `100` | Trọng số makespan |
| `SCHEDULING_MACHINE_IDLE_WEIGHT` | `10` | Trọng số idle máy |
| `SCHEDULING_MACHINE_BALANCE_WEIGHT` | `10` | Cân bằng tải giữa máy cùng operation |
| `SCHEDULING_JOB_WAIT_WEIGHT` | `10` | Thời gian chờ giữa hai công đoạn của job |
| `SCHEDULING_HYBRID_TOTAL_SECONDS` | `300` | Ngân sách chung cho hybrid |
| `SCHEDULING_COMPACT_HYBRID` | `True` | Dồn chuỗi công đoạn sau khi sửa route |

Đặt trọng số về `0` để tắt thành phần tương ứng. Tăng `NUM_WORKERS` hoặc timeout sẽ lâu hơn nhưng không khắc phục được ràng buộc vốn không khả thi.

## Kiểm thử

```bash
uv run --locked python -m unittest discover -s tests -v
```

Test dùng `unittest` có sẵn, phủ định dạng dữ liệu, deadline 70%, gộp downtime, giảm batch, ba chế độ xếp lịch, dồn lịch và xuất file.

## Giới hạn

- Chốt batch lần lượt nên không tối ưu toàn cục; kết quả phụ thuộc thứ tự job và batch size.
- Deadline 70% không bảo đảm hoàn thành 100% hay giao hàng đúng hạn.
- Chỉ mô hình hóa máy: chưa có công nhân, khuôn, vật tư, setup/changeover.
- Công đoạn không chia nhỏ (không split lot, không transfer batch).
- Chưa có release time, phụ thuộc giữa job, API hay UI.
- Kiểm tra dữ liệu đầu vào chưa phải schema đầy đủ.

## Tài liệu chi tiết

Bản giải thích đầy đủ — ví dụ từng bước về deadline 70%, công thức idle, cơ chế hybrid, toàn bộ tham số và FAQ — nằm trong [docs/chi-tiet.md](docs/chi-tiet.md).
