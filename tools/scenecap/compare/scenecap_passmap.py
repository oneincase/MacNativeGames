#!/usr/bin/env python3
"""scenecap-passmap: 把 .nvs 包的 manifest 汇总成「pass → FS 键 → draw 统计」表。

用法:
    tools/scenecap/compare/scenecap_passmap.py <package.nvs> [--pass pass/21] [--min-vc 1000]

管线键格式是 r|<vs_key>-<后缀>|<fs_key>-<后缀>|<hash>，本工具截前 8 位显示。
每行: pass id / 尺寸 / 像素格式 / draw 数 / 顶点数 / FS(vS) x次数 [dt dw df blend]
--pass 只看一个 pass 并逐 draw 列出（vc、实例、纹理绑定）。
"""
import argparse, collections, json, sys


def pipe_keys(pipes, pid):
    parts = pipes[pid]["key"].split("|")
    vs = parts[1].split("-")[0] if len(parts) > 1 else "?"
    fs = parts[2].split("-")[0] if len(parts) > 2 else "?"
    return vs, fs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("package")
    ap.add_argument("--pass", dest="one", help="只列这个 pass 的逐 draw 明细")
    ap.add_argument("--min-vc", type=int, default=0, help="明细里只显示 vc >= 该值")
    a = ap.parse_args()

    sys.path.insert(0, __file__.rsplit("/", 2)[0] + "/python")
    import scenecap_pkg
    manifest = scenecap_pkg.read_package(a.package).manifest

    pipes = {p["id"]: p for p in manifest["pipelines"]}
    by_pass = collections.defaultdict(list)
    for d in manifest["draws"]:
        by_pass[d["pass"]].append(d)

    passes = {p["id"]: p for p in manifest["passes"]}
    if a.one:
        p = passes.get(a.one)
        if not p:
            sys.exit(f"no such pass: {a.one}")
        print(f"{p['id']} {p['width']}x{p['height']} {p['pixel_format']}")
        for at in p.get("attachments", []):
            print(f"  attachment slot={at['slot']} tex={at['texture']} load={at['load']}")
        for d in by_pass.get(a.one, []):
            vs, fs = pipe_keys(pipes, d["pipeline"])
            st = pipes[d["pipeline"]]["state"]
            dep = st.get("depth_stencil", {})
            vc = d.get("index", {}).get("count") or d.get("vertex", {}).get("count") or 0
            if vc < a.min_vc:
                continue
            texs = [t["texture"] for t in d["argument_table"].get("textures", [])][:6]
            print(f"  {d['id']:<10} vc={vc:<7} inst={d.get('instances',1):<4} fs={fs[:8]} vs={vs[:8]} "
                  f"dt={dep.get('depth_test')} dw={dep.get('depth_write')} df={dep.get('depth_compare')} tex={texs}")
        return

    print(f"{'pass':<9} {'sz':<11} {'fmt':<15} {'draws':>5} {'verts':>10}  fs(vs)xdraws")
    for p in manifest["passes"]:
        ds = by_pass.get(p["id"], [])
        if not ds:
            continue
        dist = collections.Counter()
        vsum = 0
        for d in ds:
            vs, fs = pipe_keys(pipes, d["pipeline"])
            dist[(fs, vs)] += 1
            vc = d.get("index", {}).get("count") or d.get("vertex", {}).get("count") or 0
            vsum += vc * d.get("instances", 1)
        top = " ".join(f"{fs[:8]}({vs[:8]})x{n}" for (fs, vs), n in dist.most_common(10))
        print(f"{p['id']:<9} {p['width']}x{p['height']:<7} {p['pixel_format']:<15} {len(ds):>5} {vsum:>10}  {top}")


if __name__ == "__main__":
    main()
