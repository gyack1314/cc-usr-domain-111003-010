"""跨来源核对：以冻结时点重建装机组件与持有关系，输出覆盖结论与逐机证据链。

事实来源仅限：
- 浏览器下载的门户附件与文书（analysis/downloads/<编号>/），经 collection_index.json 摘要绑定；
- 本地材料 materials/devices.csv、materials/policy.md、materials/service.md。
序列区间、履历排序、字段投影通过项目领域工具（python -m workbench.tools call）计算。
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call_tool(tool: str, payload: dict, workspace: Path):
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        out = subprocess.run(
            [sys.executable, "-m", "workbench.tools", "call", tool, tmp],
            cwd=str(workspace), capture_output=True, text=True)
        if out.returncode != 0:
            raise RuntimeError(f"领域工具 {tool} 失败: {out.stderr.strip()}")
        return json.loads(out.stdout)
    finally:
        os.unlink(tmp)


def parse_document_fields(text: str) -> dict:
    """解析文书副本中的 key：value 行（值为 JSON 字面量）。"""
    fields = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_]+)：(.+)$", line.strip())
        if m:
            fields[m.group(1)] = json.loads(m.group(2))
    return fields


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--downloads", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--devices", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--service", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--notes", required=True)
    args = ap.parse_args()

    downloads = Path(args.downloads)
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    (outdir / "evidence").mkdir(parents=True, exist_ok=True)
    notes_dir = Path(args.notes)
    notes_dir.mkdir(parents=True, exist_ok=True)

    # ---- 规程：冻结时点 ----
    policy_text = Path(args.policy).read_text(encoding="utf-8")
    m = re.search(r"冻结时点([0-9T:+\-]+)", policy_text)
    if not m:
        raise RuntimeError("规程未给出冻结时点")
    freeze = m.group(1)
    cutoff = freeze[:10]
    service_text = Path(args.service).read_text(encoding="utf-8")
    m2 = re.search(r"安全件前缀([A-Z]+)", service_text)
    safe_prefix = m2.group(1) if m2 else None

    # ---- 载入下载事实并校验摘要绑定 ----
    records = {}
    doc_checks = {}
    for rid, meta in index["records"].items():
        rdir = downloads / rid
        att = rdir / "attachment.json"
        if sha256_file(att) != meta["attachment_sha256"]:
            raise RuntimeError(f"{rid} 附件摘要与采集索引不符")
        record = json.loads(att.read_text(encoding="utf-8"))
        doc = rdir / meta["document_file"]
        if sha256_file(doc) != meta["document_sha256"]:
            raise RuntimeError(f"{rid} 文书摘要与采集索引不符")
        doc_fields = parse_document_fields(doc.read_text(encoding="utf-8"))
        # 文书原文与结构化字段必须一致（范围、起止、排除以原文为准的交叉核对）
        consistent = doc_fields == record["fields"]
        doc_checks[rid] = {"document_fields_equal_json": consistent}
        if not consistent:
            raise RuntimeError(f"{rid} 文书原文与结构化字段不一致: {doc_fields} != {record['fields']}")
        records[rid] = record

    def ref(rid):
        return f"{rid}@{index['records'][rid]['attachment_sha256']}"

    # ---- 通告谱系：撤回不扩张范围，仅保留关联 ----
    notices = {r["id"]: r for r in records.values() if r["category"] == "通告"}
    withdrawals = {r["id"]: r for r in records.values() if r["category"] == "撤回"}
    withdrawn = {w["fields"]["target"] for w in withdrawals.values()}
    current = [n for n in notices.values() if n["id"] not in withdrawn]
    if len(current) != 1:
        raise RuntimeError(f"现行通告不唯一: {[n['id'] for n in current]}")
    notice = current[0]
    nf = notice["fields"]
    for w in withdrawals.values():
        if w["fields"]["replacement"] != notice["id"]:
            raise RuntimeError(f"撤回 {w['id']} 的替代通告不是现行通告")
    if nf.get("replaces") not in withdrawn:
        raise RuntimeError("现行通告未声明替代被撤回通告")
    if not nf.get("inclusive"):
        raise RuntimeError("通告区间非闭区间，需人工确认起止")
    excluded_clause = set(nf.get("excluded", []))
    notice_scope = {"prefix": nf["prefix"], "lo": nf["lo"], "hi": nf["hi"],
                    "inclusive": True, "excluded": sorted(excluded_clause)}

    exclusions = {r["fields"]["component"]: r["id"]
                  for r in records.values() if r["category"] == "排除"}
    repairs = [r for r in records.values() if r["category"] == "维修"]
    transfers = [r for r in records.values() if r["category"] == "持有"]

    # ---- 登记设备 ----
    with open(args.devices, encoding="utf-8", newline="") as f:
        devices = list(csv.DictReader(f))
    if len({d["device"] for d in devices}) != len(devices):
        raise RuntimeError("整机登记存在重复编号")

    def in_scope_range(serial):
        return call_tool("in_range", {"serial": serial, "prefix": nf["prefix"],
                                      "lo": nf["lo"], "hi": nf["hi"]}, Path(args.workspace))

    def serial_num(serial):
        mnum = re.fullmatch(re.escape(nf["prefix"]) + r"(\d+)", serial)
        return int(mnum.group(1)) if mnum else None

    coverage_devices = {}
    evidence_dir = outdir / "evidence"
    record_usage = {rid: {"used_for": []} for rid in records}
    for rid in [notice["id"], *withdrawn, *withdrawals]:
        record_usage[rid]["used_for"].append("通告谱系")

    for dev in devices:
        sn = dev["device"]
        factory = dev["factory_ps"]
        issues = []
        # 履历：仅取本机记录（维修/持有不跨机套用），按冻结时点截断并排序
        dev_repairs = [r for r in repairs if r["fields"]["device"] == sn]
        dev_transfers = [r for r in transfers if r["fields"]["device"] == sn]
        events = ([{"id": r["id"], "date": r["fields"]["date"], "kind": "维修", "fields": r["fields"]}
                   for r in dev_repairs]
                  + [{"id": r["id"], "date": r["fields"]["effective"], "kind": "持有", "fields": r["fields"]}
                     for r in dev_transfers])
        ordered = call_tool("history", {"events": events, "cutoff": "9999-12-31"}, Path(args.workspace))
        at_freeze = [e for e in ordered if e["date"] <= cutoff]
        post_freeze = [e for e in ordered if e["date"] > cutoff]
        same_day = [e["id"] for e in ordered if e["date"] == cutoff]
        if same_day:
            issues.append(f"事件 {same_day} 日期与冻结日相同，时刻粒度需人工确认")

        # 装机组件重建：仅采信 完成且有签收凭证 的维修；链式一致性校验
        # 区分两类未采信：completed=false 为未完成（无争议，维持原状）；
        # completed=true 但无签收为"声称已换但凭证缺失"，须人工核实。
        installed = factory
        applied, unproven, not_done = [], [], []
        for e in [e for e in at_freeze if e["kind"] == "维修"]:
            f = e["fields"]
            if f["old"] != installed:
                issues.append(f"维修 {e['id']} 记录旧件 {f['old']} 与当时装机 {installed} 不符")
            if f["completed"] and f.get("receipt"):
                installed = f["new"]
                applied.append(e["id"])
                record_usage[e["id"]]["used_for"].append(f"{sn} 装机组件重建")
            elif f["completed"]:
                unproven.append(e["id"])
                record_usage[e["id"]]["used_for"].append(f"{sn} 声称已换但签收凭证缺失")
            else:
                not_done.append(e["id"])
                record_usage[e["id"]]["used_for"].append(f"{sn} 维修未完成（申请/计划，不证明已更换）")

        # 持有重建：须证明生效；确认有效的变更若不能构成唯一一致链（同一持有人
        # 同时转出给不同接收方），则整组冲突留置，不按排序先后择一采信。
        holder = dev["dealer"]
        ownership_status = "登记持有"
        held_transfers = []
        applied_transfers = []
        confirmed = [e for e in at_freeze if e["kind"] == "持有" and e["fields"].get("confirmed")]
        unconfirmed = [e for e in at_freeze if e["kind"] == "持有" and not e["fields"].get("confirmed")]
        for e in unconfirmed:
            issues.append(f"持有变更 {e['id']} 未确认生效，不采信")
        remaining = list(confirmed)
        conflict = False
        while remaining:
            candidates = [e for e in remaining if e["fields"]["from_dealer"] == holder]
            if not candidates:
                conflict = True
                break
            destinations = {e["fields"]["to_dealer"] for e in candidates}
            if len(destinations) > 1:
                conflict = True
                break
            chosen = candidates[0]
            holder = chosen["fields"]["to_dealer"]
            applied_transfers.append(chosen["id"])
            record_usage[chosen["id"]]["used_for"].append(f"{sn} 持有重建")
            remaining.remove(chosen)
        if conflict:
            ownership_status = "冲突留置"
            held_transfers = [e["id"] for e in confirmed]
            applied_transfers = []
            for e in confirmed:
                record_usage[e["id"]]["used_for"].append(f"{sn} 持有冲突（留置）")
            holder_at_freeze = None
        else:
            holder_at_freeze = holder

        # 范围判定（按现行通告原文；撤回通告不参与）
        range_hit = in_scope_range(installed)
        num = serial_num(installed)
        clause_excluded = bool(range_hit and num is not None and num in excluded_clause)
        ex_record = exclusions.get(installed) if range_hit else None
        in_scope = bool(range_hit and not clause_excluded and not ex_record)

        basis = []
        if in_scope:
            if unproven:
                issues.append(f"换件记录 {unproven} 声称已完成但无签收凭证，不能视为已消除风险，须人工核实")
            if conflict:
                issues.append(f"持有变更 {held_transfers} 相互冲突，责任经销商留置待裁定")
            category = "manual_review" if issues else "needs_recall"
            basis.append(ref(notice["id"]))
        else:
            if clause_excluded or ex_record:
                category = "excluded_with_basis"
                basis.append(ref(notice["id"]))
                if ex_record:
                    basis.append(ref(ex_record))
                    record_usage[ex_record]["used_for"].append(f"{sn} 排除依据")
            elif applied and in_scope_range(factory) and not range_hit:
                category = "excluded_with_basis"  # 已核实更换为范围外组件
                basis.extend(ref(a) for a in applied)
            else:
                category = "not_affected"
                basis.append(ref(notice["id"]))

        for e in post_freeze:
            record_usage[e["id"]]["used_for"].append(f"{sn} 冻结后记录（不改变冻结结论）")

        evidence = {
            "device": sn,
            "freeze": freeze,
            "registration": {"factory_ps": factory, "dealer": dev["dealer"],
                             "contact": dev["contact"], "source": "materials/devices.csv"},
            "events_at_or_before_freeze": [
                {"ref": ref(e["id"]), "date": e["date"], "kind": e["kind"],
                 "applied": e["id"] in applied or e["id"] in applied_transfers}
                for e in at_freeze],
            "post_freeze_records": [{"ref": ref(e["id"]), "date": e["date"], "kind": e["kind"]}
                                    for e in post_freeze],
            "installed_at_freeze": installed,
            "factory_ps": factory,
            "repairs": {"applied": [ref(a) for a in applied],
                        "claimed_but_no_receipt": [ref(u) for u in unproven],
                        "not_completed": [ref(n) for n in not_done]},
            "scope": {"notice": ref(notice["id"]), "scope_text": notice_scope,
                      "range_hit": range_hit, "clause_excluded": clause_excluded,
                      "exclusion_record": ref(ex_record) if ex_record else None,
                      "in_scope": in_scope},
            "ownership_at_freeze": {"dealer": holder_at_freeze, "status": ownership_status},
            "classification": category,
            "basis_refs": basis,
            "open_issues": issues,
        }
        (evidence_dir / f"{sn}.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        coverage_devices[sn] = {
            "classification": category,
            "installed_at_freeze": installed,
            "factory_ps": factory,
            "dealer_at_freeze": holder_at_freeze,
            "registered_dealer": dev["dealer"],
            "contact": dev["contact"],
            "in_scope": in_scope,
            "open_issues": issues,
            "evidence": f"evidence/{sn}.json",
        }

    # ---- 总量守恒 ----
    cats = {}
    for sn, d in coverage_devices.items():
        cats.setdefault(d["classification"], []).append(sn)
    notify = {sn: d for sn, d in coverage_devices.items() if d["classification"] == "needs_recall"}
    affected_installed = sorted(d["installed_at_freeze"] for d in coverage_devices.values() if d["in_scope"])
    removed_affected = []
    for dev in devices:
        sn = dev["device"]
        d = coverage_devices[sn]
        if d["installed_at_freeze"] != dev["factory_ps"] and in_scope_range(dev["factory_ps"]):
            removed_affected.append({"device": sn, "component": dev["factory_ps"]})
    conservation = {
        "registered_devices": len(devices),
        "classified_devices": len(coverage_devices),
        "by_category": {k: sorted(v) for k, v in sorted(cats.items())},
        "category_total": sum(len(v) for v in cats.values()),
        "affected_components_installed": affected_installed,
        "affected_components_count": len(affected_installed),
        "notify_devices_count": len(notify),
        "removed_affected_components": removed_affected,
    }
    if conservation["category_total"] != len(devices):
        raise RuntimeError("总量不守恒：分类合计 != 登记设备数")
    if len(affected_installed) != len(set(affected_installed)):
        raise RuntimeError("受影响组件重复计数")

    coverage = {
        "freeze": freeze,
        "safe_prefix": safe_prefix,
        "notice": {
            "current": ref(notice["id"]),
            "scope": notice_scope,
            "effective": nf.get("effective"),
            "lineage": {
                "replaces": ref(nf["replaces"]),
                "withdrawn_by": [ref(w["id"]) for w in withdrawals.values()
                                 if w["fields"]["target"] == nf["replaces"]],
                "rule": "撤回通告仅保留与现行通告的关联，不参与范围扩张",
            },
        },
        "devices": coverage_devices,
        "conservation": conservation,
        "record_usage": {rid: meta["used_for"] for rid, meta in sorted(record_usage.items())},
        "document_checks": doc_checks,
        "source_index_sha256": sha256_file(Path(args.index)),
    }
    (outdir / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note = {"freeze": freeze, "notice": coverage["notice"]["current"],
            "by_category": conservation["by_category"],
            "source_index_sha256": coverage["source_index_sha256"]}
    (notes_dir / "coverage.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(note, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
