# Plan: Bổ sung ArXiv làm nguồn dữ liệu thứ hai

## Mục tiêu

Bổ sung ArXiv Atom API song song với luồng PWC sitemap hiện tại để:
- Crawl toàn bộ paper mới hàng ngày (không giới hạn theo PWC index)
- Lấy abstract trực tiếp từ API (không cần scrape HTML)
- Extract keyword từ abstract bằng OpenAI LLM

---

## Hiện trạng

```
Celery Beat (1:00 AM daily)
      │
      ▼
check_pwc_sitemap_and_create_tasks()   ← nguồn duy nhất hiện tại
      │ paperswithcode.com/sitemap.xml
      │ Coverage: chỉ paper có code trên PWC, lag ~1-2 ngày
      ▼
crawl_paper_details() → download_pdf()
```

---

## Kiến trúc đề xuất

```
Celery Beat
      │
      ├── check_pwc_sitemap_and_create_tasks()   [giữ nguyên]
      │
      └── [NEW] crawl_arxiv_new_papers()         [thêm mới]
                    │ export.arxiv.org/api/query
                    │ Lấy toàn bộ paper mới trong ngày
                    │ Abstract có sẵn trong response
                    ▼
              extract_keywords_with_llm(abstract)
                    │ OpenAI API
                    ▼
              Ghi DB (Paper + Keywords)
```

---

## Chi tiết luồng ArXiv

### 1. Fetch paper mới

```
GET https://export.arxiv.org/api/query
    ?search_query=cat:cs.*
    &sortBy=submittedDate
    &sortOrder=descending
    &max_results=100
    &start=0
```

- Phân trang: tăng `start` theo batch cho đến khi hết paper trong ngày
- Filter: chỉ lấy paper có `published` >= ngày hôm nay
- Deduplication: check `arxiv_id` hoặc URL trước khi tạo CrawlTask

### 2. Parse Atom XML response

Các trường lấy được từ API:

| Trường API | Trường DB |
|---|---|
| `<title>` | `Paper.title` |
| `<summary>` | `Paper.abstract` |
| `<author><name>` | `Author` (M2M) |
| `<category term>` | `Category` (M2M) |
| `<published>` | `Paper.publication_date` |
| `<id>` (arxiv URL) | `Paper.url`, `CrawlTask.url` |
| `<link rel="related">` | `Paper.pdf_url` |

Các trường **không có** từ ArXiv API (vẫn cần PWC):
- GitHub URL
- Datasets / Benchmarks
- Leaderboard data

### 3. LLM Keyword Extraction

- **Model:** OpenAI (key lấy từ `settings.OPENAI_API_KEY`)
- **Input:** `Paper.abstract`
- **Skip nếu:** abstract `None` hoặc < 50 ký tự
- **Output:** comma-separated string, lưu vào `Paper.keywords`
- **Retry:** 3 lần nếu timeout/rate limit, fallback về `""`

Prompt:
```
Given the following research paper abstract, extract 8-10 keywords
that best represent the core topics, methods, and contributions.
Return as a comma-separated list of short phrases (1-3 words each).
Normalize to standard academic terms where possible.

Abstract: {abstract}
```

---

## Thay đổi cần làm

### requirements.txt
```
openai
```

### crawler/tasks.py
- Thêm task `crawl_arxiv_new_papers()`:
  - Fetch + phân trang ArXiv API
  - Deduplication theo URL
  - Tạo CrawlTask và Paper records
  - Gọi `extract_keywords_with_llm()`
- Thêm helper `fetch_arxiv_page(start, max_results)` để phân trang
- Thêm helper `parse_arxiv_atom(xml_content)` để parse XML

### crawler/parsers.py
- Thêm `extract_keywords_with_llm(abstract: str) -> str`
  - Init OpenAI client
  - Gọi API với prompt chuẩn
  - Parse và return comma-separated string
- Giữ nguyên `extract_keywords()` cũ (dùng cho PWC flow)

### crawler_service/settings.py
- Thêm:
  ```python
  OPENAI_API_KEY = env('OPENAI_API_KEY', default='')
  OPENAI_MODEL = env('OPENAI_MODEL', default='gpt-4o-mini')
  ```
- Thêm Celery Beat schedule cho task mới:
  ```python
  'crawl-arxiv-daily': {
      'task': 'crawler.tasks.crawl_arxiv_new_papers',
      'schedule': crontab(hour=2, minute=0),  # 2:00 AM UTC
  }
  ```

### crawler/models.py
- Không thay đổi — `keywords = CharField(max_length=200)` giữ nguyên

### .env
- Thêm `OPENAI_API_KEY`
- Thêm `OPENAI_MODEL` (optional, default gpt-4o-mini)

---

## Không thay đổi

- Toàn bộ luồng PWC sitemap hiện tại
- `crawl_paper_details()`, `crawl_dataset()`, `download_pdf()`
- Database schema (trừ thêm `openai` vào requirements)
- Docker compose

---

## Rủi ro & lưu ý

| Rủi ro | Xử lý |
|---|---|
| ArXiv rate limit (3 req/s) | Thêm delay giữa các batch request |
| Paper trùng giữa PWC và ArXiv | Deduplication theo URL trước khi tạo CrawlTask |
| OpenAI cost tăng theo volume | Dùng `gpt-4o-mini` (rẻ hơn 10x so với gpt-4o), chỉ gọi 1 lần/paper |
| Abstract quá dài → vượt token limit | Truncate abstract về 1000 ký tự trước khi gửi |

---

## Open questions

- [ ] Có cần lưu `arxiv_id` riêng vào DB để dedup dễ hơn không?
- [ ] PWC flow có cần bổ sung keyword extraction bằng LLM không (hiện chỉ ArXiv flow mới có)?
