"""通过浏览器从本地门户收集事实材料。

- 遍历资料目录全部分页，取得资料编号清单（不读服务端数据文件）。
- 对每份资料：打开详情页，读取页面展示的摘要与引用，下载原始 JSON 附件与文书副本。
- 每份资料的下载物放入 downloads/<编号>/ 独立新目录；已存在且摘要相符则复用（幂等恢复），
  摘要冲突则报错，不静默覆盖。
- 校验：下载附件字节的 SHA-256 必须等于页面展示的摘要，且引用为 编号@摘要。
- 输出 collection_index.json（派生索引，放在 downloads 之外）。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(url: str, downloads: Path, index_path: Path) -> dict:
    downloads = Path(downloads)
    downloads.mkdir(parents=True, exist_ok=True)
    result = {"portal": url, "records": {}, "catalog_pages": []}
    run_report = {"reused": [], "downloaded": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 720}, accept_downloads=True)

        # 1) 目录分页，收集全部资料编号
        ids = []
        page_no = 1
        while True:
            page.goto(f"{url}/?page={page_no}", wait_until="domcontentloaded")
            rows = page.locator("tr[data-record-id]")
            found = rows.evaluate_all("els => els.map(e => e.getAttribute('data-record-id'))")
            if not found:
                break
            count_text = page.locator('[data-testid="count"]').inner_text()
            result["catalog_pages"].append({"page": page_no, "ids": found, "count_text": count_text})
            ids.extend(found)
            page_no += 1
            if page_no > 50:
                raise RuntimeError("分页异常：超过 50 页")
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"目录出现重复编号: {ids}")

        # 2) 逐份资料：详情页 + 附件 + 文书
        for rid in ids:
            page.goto(f"{url}/record/{rid}", wait_until="domcontentloaded")
            title = page.locator("h1").inner_text()
            shown_id = page.locator('[data-testid="record-id"]').inner_text()
            digest = page.locator('[data-testid="digest"]').inner_text()
            reference = page.locator('[data-testid="reference"]').inner_text()
            fields_text = page.locator('[data-testid="fields"]').inner_text()
            if shown_id != rid:
                raise RuntimeError(f"页面编号不一致: 目录 {rid} vs 详情 {shown_id}")
            if reference != f"{rid}@{digest}":
                raise RuntimeError(f"{rid} 引用格式异常: {reference}")

            rdir = downloads / rid
            attachment = rdir / "attachment.json"
            reused = False
            if rdir.exists():
                # 恢复路径：已有下载物必须仍与页面摘要相符，否则拒绝继续
                if not attachment.is_file():
                    raise RuntimeError(f"{rdir} 已存在但缺少附件，需人工清理")
                if sha256(attachment.read_bytes()) != digest:
                    raise RuntimeError(f"{rid} 已下载附件与门户当前摘要不符，需人工核对")
                reused = True
            else:
                rdir.mkdir()
                with page.expect_download() as dl:
                    page.get_by_role("link", name="下载原始材料").click()
                dl.value.save_as(attachment)
                with page.expect_download() as dl2:
                    page.get_by_role("link", name="下载文书或扫描副本").click()
                dl2.value.save_as(rdir / dl2.value.suggested_filename)
                page.screenshot(path=str(rdir / "page.png"), full_page=True)

            att_bytes = attachment.read_bytes()
            att_sha = sha256(att_bytes)
            if att_sha != digest:
                raise RuntimeError(f"{rid} 附件摘要 {att_sha} != 页面摘要 {digest}")
            record = json.loads(att_bytes.decode("utf-8"))
            if record.get("id") != rid:
                raise RuntimeError(f"{rid} 附件内容编号为 {record.get('id')}")
            if json.dumps(record["fields"], ensure_ascii=False, sort_keys=True) != json.dumps(
                json.loads(fields_text), ensure_ascii=False, sort_keys=True
            ):
                raise RuntimeError(f"{rid} 页面展示字段与附件字段不一致")

            docs = [f for f in sorted(rdir.iterdir()) if f.name not in ("attachment.json", "page.png")]
            if not docs:
                raise RuntimeError(f"{rid} 缺少文书副本")
            result["records"][rid] = {
                "title": title,
                "category": record.get("category"),
                "revision": record.get("revision"),
                "reference": reference,
                "attachment_sha256": att_sha,
                "document_file": docs[0].name,
                "document_sha256": sha256(docs[0].read_bytes()),
            }
            run_report["reused" if reused else "downloaded"].append(rid)
        browser.close()
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(index_path)
    run_path = index_path.parent / "collection_last_run.json"
    run_path.write_text(json.dumps(run_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--downloads", required=True)
    ap.add_argument("--index", required=True)
    args = ap.parse_args()
    result = collect(args.url, args.downloads, args.index)
    print(json.dumps({"records": len(result["records"]),
                      "ids": sorted(result["records"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
