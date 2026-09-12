# このファイルは複数台操作の結果を人間が読みやすい形式に整形する。


def _label(host):
    if host.machine_no >= 0:
        return f"machine_no={host.machine_no} ip={host.ip}"
    return f"ip={host.ip}"


def format_results(results, *, title=None):
    lines = []
    if title:
        lines.append(title)

    ok_count = 0
    fail_count = 0
    for result in results:
        label = _label(result.host)
        if getattr(result, "skipped", False):
            lines.append(f"  [SKIP] {label}: {result.error}")
            ok_count += 1
            continue

        if result.ok:
            commit = getattr(result, "commit", "")
            extra = f" commit={commit[:12]}" if commit else ""
            stage = getattr(result, "stage", "")
            stage_suffix = f" ({stage})" if stage else ""
            lines.append(f"  [ OK ] {label}{extra}{stage_suffix}")
            warning = getattr(result, "warning", "")
            if warning:
                lines.append(f"         [WARN] {warning}")
            ok_count += 1
        else:
            stage = getattr(result, "stage", "?")
            lines.append(f"  [FAIL] {label} stage={stage}: {result.error}")
            fail_count += 1

    lines.append(f"合計: 成功 {ok_count} / 失敗 {fail_count} / 全 {len(results)}")
    return "\n".join(lines)
