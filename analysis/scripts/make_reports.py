"""由 coverage.json 与逐机证据链生成：
- 服务经理审核摘要（内部，含争议与未决项）
- 按经销商拆分的通知草稿（仅本商设备与必要联系人，标明尚未发出）
- 私有内部备注（跨经销商名册与争议细节，不随通知发出）
输出确定性内容（不含墙钟时间），便于进度账幂等。
"""
import argparse
import json
from pathlib import Path

CATEGORY_LABEL = {
    "needs_recall": "需召回",
    "excluded_with_basis": "排除有据",
    "manual_review": "人工核实",
    "not_affected": "不在范围",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--notes", required=True)
    args = ap.parse_args()

    outdir = Path(args.outdir)
    coverage = json.loads(Path(args.coverage).read_text(encoding="utf-8"))
    template = Path(args.template).read_text(encoding="utf-8").strip()
    freeze = coverage["freeze"]
    scope = coverage["notice"]["scope"]
    devices = coverage["devices"]
    evidence = {sn: json.loads((outdir / d["evidence"]).read_text(encoding="utf-8"))
                for sn, d in devices.items()}

    scope_text = (f"前缀 {scope['prefix']}，序号 {scope['lo']}–{scope['hi']} 闭区间"
                  f"，排除 {', '.join(str(n) for n in scope['excluded']) or '无'}")

    # ---- 服务经理摘要 ----
    lines = [
        "# 召回范围核对与分层通知准备摘要（服务经理审核稿）",
        "",
        "状态：内部预审材料，全部通知草稿尚未发出，未联系任何客户；不构成召回完成或对外承诺。",
        "",
        "## 依据与方法",
        f"- 冻结时点：{freeze}（以 materials/policy.md 为准）。",
        f"- 现行通告：{coverage['notice']['current']}，生效 {coverage['notice']['effective']}；范围按通告原文：{scope_text}。",
        f"- 谱系：现行通告替代 {coverage['notice']['lineage']['replaces']}（撤回记录 "
        + "、".join(coverage["notice"]["lineage"]["withdrawn_by"]) + "）；撤回通告仅保留关联，不参与范围扩张。",
        f"- 安全件前缀 {coverage['safe_prefix']}（materials/service.md）；整机 SN 与组件 PS 序列分别核对，维修记录不跨机套用。",
        "- 维修仅采信冻结时点前完成且有签收凭证者；持有变更须确认生效，冲突留置。",
        "- 事实来源：浏览器自本地门户下载的 12 份资料附件与文书（摘要绑定见 coverage.json），本地整机登记 materials/devices.csv。",
        "",
        "## 分类结论（总量守恒：8 = 3 + 2 + 1 + 2）",
        "",
        "| 整机 | 出厂组件 | 冻结时点装机组件 | 冻结时点经销商 | 分类 | 依据/问题 |",
        "|---|---|---|---|---|---|",
    ]
    for sn in sorted(devices):
        d = devices[sn]
        e = evidence[sn]
        dealer = d["dealer_at_freeze"] or "（留置待定）"
        note = "；".join(d["open_issues"]) if d["open_issues"] else "、".join(
            b.split("@")[0] for b in e["basis_refs"])
        lines.append(f"| {sn} | {d['factory_ps']} | {d['installed_at_freeze']} | {dealer} "
                     f"| {CATEGORY_LABEL[d['classification']]} | {note} |")
    lines += [
        "",
        "## 需召回（3 台，进入经销商通知草稿）",
        "",
    ]
    for sn in sorted(s for s, d in devices.items() if d["classification"] == "needs_recall"):
        d = devices[sn]
        lines.append(f"- {sn}（{d['dealer_at_freeze']}）：装机 {d['installed_at_freeze']} 在通告范围内。")
    lines += [
        "",
        "## 排除有据（2 台，不通知）",
        "",
        "- SN01：出厂 PSA0100 在范围内，但冻结前已完成换件并有签收凭证（M1/OK-1），现装安全件 PSC0001。",
        "- SN07：PSA0150 被现行通告排除条款（150）排除，并有逐机排除说明 EX（认证替代材料）。",
        "",
        "## 必须人工核实（1 台，暂不入任何经销商通知）",
        "",
        "- SN08：装机 PSA0151 在范围内；M4 声称已完成换件但无签收凭证，不能视为已消除风险；"
        "H2（转 D东）与 H3（转 D北）同日生效且均确认，持有冲突留置，责任经销商待裁定。",
        "",
        "## 不在范围（2 台）",
        "",
        "- SN04：PSB0101 前缀与通告前缀不同，按规程不可比较，不在范围。",
        "- SN05：PSA0099 序号低于区间下限 100，不在范围（持有已于冻结前转至 D西）。",
        "",
        "## 未决问题（请服务经理裁决/跟进）",
        "",
        "1. SN08 换件凭证与持有冲突：需调取签收凭证并裁定责任经销商后再决定通知归属。",
        "2. SN01 拆下的受影响旧件 PSA0100 去向无记录，建议核查是否流入维修库存（SN06 即维修时装入受影响库存件 PSA0105 的案例）。",
        "3. SN03 存在冻结后维修记录 M5（2026-11-11，签收 OK-5）：不改变冻结时点结论，实际执行通知前建议复核当前组件。",
        "4. SN02 的 M3 仅为换件申请（未完成），不证明已更换，SN02 维持需召回。",
        "",
        "## 通知计划（草稿均未发出）",
        "",
        "- D东：SN02（1 台）。",
        "- D西：SN03、SN06（2 台）。",
        "- SN08 留置，不进入任何经销商草稿；排除与不在范围设备不通知。",
        "- 各经销商草稿仅含本商设备与必要联系人；跨经销商名册与争议备注见私有材料（不随草稿发出）。",
        "",
    ]
    (outdir / "manager_summary.md").write_text("\n".join(lines), encoding="utf-8")

    # ---- 经销商通知草稿（按经销商拆分，字段最小化）----
    dealers_dir = outdir / "dealers"
    dealers_dir.mkdir(exist_ok=True)
    by_dealer = {}
    for sn, d in devices.items():
        if d["classification"] == "needs_recall":
            by_dealer.setdefault(d["dealer_at_freeze"], []).append(sn)
    dealer_files = {}
    for dealer, sns in sorted(by_dealer.items()):
        rows = [
            "【内部预审草稿 · 尚未发出 · 待负责人批准】",
            "",
            template,
            "",
            f"依据现行召回通告（{scope_text}），请核实贵方负责的以下设备（冻结时点 {freeze} 的装机组件）：",
            "",
            "| 整机SN | 冻结时点装机组件 | 设备联系人 | 请处理事项 |",
            "|---|---|---|---|",
        ]
        for sn in sorted(sns):
            d = devices[sn]
            rows.append(f"| {sn} | {d['installed_at_freeze']} | {d['contact']} "
                        f"| 核实当前组件序列，如仍为上列组件请更换为安全件（前缀 {coverage['safe_prefix']}） |")
        rows += ["", "本草稿尚未发出，不附内部争议备注。", ""]
        path = dealers_dir / f"通知草稿-{dealer}.md"
        path.write_text("\n".join(rows), encoding="utf-8")
        dealer_files[dealer] = path.name

    # ---- 私有内部备注（跨经销商名册 + 争议细节）----
    priv = [
        "# 内部私有材料（不得随经销商通知发出）",
        "",
        "## 跨经销商全量名册（冻结时点持有）",
        "",
        "| 整机 | 冻结时点经销商 | 分类 | 联系人 |",
        "|---|---|---|---|",
    ]
    for sn in sorted(devices):
        d = devices[sn]
        priv.append(f"| {sn} | {d['dealer_at_freeze'] or '留置待定'} "
                    f"| {CATEGORY_LABEL[d['classification']]} | {d['contact']} |")
    priv += [
        "",
        "## 争议与留置细节",
        "",
        "- SN08：H2（D西→D东，2026-11-09 生效，已确认）与 H3（D西→D北，同日生效，已确认）冲突，"
        "两份凭证不能同时成立，持有留置待裁定；M4 换件 completed=true 但 receipt 为空。",
        "- SN01 拆下旧件 PSA0100（受影响范围）去向无记录。",
        "- SN03 冻结后维修 M5（2026-11-11，OK-5）不计入冻结时点状态。",
        "",
    ]
    (outdir / "private" / "内部备注.md").write_text("\n".join(priv), encoding="utf-8")

    import hashlib
    note = {"files": {}}
    for p in [outdir / "manager_summary.md", outdir / "private" / "内部备注.md",
              *[dealers_dir / n for n in dealer_files.values()]]:
        note["files"][p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    notes_dir = Path(args.notes)
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "reports.json").write_text(
        json.dumps(note, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"dealers": dealer_files, "files": sorted(note["files"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
