"""Build the corpus graph, one video at a time, resumable.

Results are appended to a .jsonl as they arrive rather than held in memory until
the end. Two runs have now been lost to that mistake — the people file, and this
— so the rule stands on its own: anything a long run learns should survive the
run being killed.
"""
import json, os, sys
import graph

CORPUS = sys.argv[1] if len(sys.argv) > 1 else "corpus/ai-engineer"
PARTS = os.path.join(CORPUS, ".graph-parts.jsonl")

done = {}
if os.path.exists(PARTS):
    for line in open(PARTS, encoding="utf-8"):
        try:
            r = json.loads(line)
            # A failed attempt is not "done" — retry it on the next run.
            if not r.get("failed"):
                done[r.get("video_id")] = r
        except ValueError:
            continue

people = {}
pf = os.path.join(CORPUS, ".transkrp-people.json")
if os.path.exists(pf):
    people = json.load(open(pf, encoding="utf-8"))

ts = graph.load(CORPUS)
results, failures = [], 0
with open(PARTS, "a", encoding="utf-8") as out:
    for i, t in enumerate(ts, 1):
        import transkrp
        vid = transkrp.video_id(t.get("url", "")) or t.get("path", "")
        if vid in done:
            results.append(done[vid])
            print(f"[{i:2}/{len(ts)}] cached  {t['title'][:44]}", flush=True)
            continue
        r = graph.extract(t, corpus=people)
        results.append(r)
        out.write(json.dumps(r, ensure_ascii=False) + "\n")
        out.flush()
        note = f"FAILED: {r['failed'][:40]}" if r.get("failed") else \
               f"people={len(r['people']):2} edges={len(r['edges']):2} rejected={r['rejected']}"
        failures += bool(r.get("failed"))
        print(f"[{i:2}/{len(ts)}] {t['title'][:44]:46} {note}", flush=True)

g = graph.merge(results)
graph.save(g, os.path.join(CORPUS, "graph.json"))
print(f"\nGRAPH: {len(g['people'])} people, {len(g['edges'])} edges, "
      f"{g['rejected']} edges rejected, {len(g['failed'])} videos failed")
