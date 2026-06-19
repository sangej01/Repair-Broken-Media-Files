# Benchmarking Guide: Finding Your Optimal `--workers` Value

Quick reference for using the built-in benchmark to determine how many parallel
scan workers your hardware can effectively use.

For day-to-day usage, see [USERGUIDE.md](USERGUIDE.md). For setting up the app
on additional PCs, see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## What This Is

`Repair Broken Media Files` ships with a `benchmark` command that:

1. Picks a small sample of movies from your library
2. Scans them at different worker counts (1, 2, 4 by default)
3. Measures wall-clock time per pass
4. Reports throughput so you can pick the optimal `--workers` value for the GUI
   and the `scan` command

The benchmark uses an **isolated temporary SQLite database** so it never
touches your real `repair.db` or your shared PostgreSQL state. You can run it
any time without consequences.

---

## Why Benchmark?

The default of `--workers 2` is a reasonable guess, but the actual optimum
depends on three things that vary between PCs and libraries:

| Factor | Effect |
|---|---|
| **Network speed** (NIC + switch + NAS NIC) | Caps total read throughput |
| **NAS disk type** (HDD vs SSD) | HDDs penalize parallel reads heavily |
| **Codec mix** (H.264 vs HEVC vs AV1) | HEVC/AV1 are CPU-bound; H.264 is network-bound |

Three real-world examples:

- **Fast NIC + SSD NAS + H.264 library**: bottleneck is network. 4-8 workers
  pays off.
- **1 GbE + HDD NAS + 1080p HEVC library**: bottleneck is CPU per file.
  2 workers wins; 4+ wastes resources.
- **10 GbE + HDD NAS + 4K HEVC library**: bottleneck is HDD seeks AND CPU.
  1-2 workers; 4 makes things worse.

Run the benchmark instead of guessing.

---

## Quick Start

```powershell
cd "Repair Broken Media Files"

# Run with defaults (4 small files, 1/2/4 workers, ~10-20 minute runtime)
.\dist\RepairBrokenMedia.exe benchmark

# Or from source
pipenv run python main.py benchmark
```

It will:

1. Show a pre-flight report (folders selected, total bytes, time estimate)
2. Ask you to confirm before starting
3. Run each pass and report per-file progress
4. Print a summary table at the end

---

## Pre-Flight Checklist

Before saying `y` to start, **stop other scanning activity** for accurate
results:

- [ ] Close the Repair Broken Media Files GUI on this PC (or at least Stop any active scan)
- [ ] Stop any other CLI scans on this PC
- [ ] If using `DB_BACKEND=postgres`, stop scans on **other PCs** sharing the
      same database (their NAS reads compete with this benchmark)
- [ ] Avoid heavy network usage (large downloads, Plex transcoding, etc.)

The benchmark uses a temporary isolated database, but it still reads files from
the same NAS as everything else.

---

## Reading the Output

### Per-Pass Output

```
=== Pass with 2 worker(s) ===
  [1/4] CLEAN    (  3.7s elapsed)  10.0 Earthquake (2014)
  [2/4] CLEAN    (241.4s elapsed)  16 Blocks (2006)
  [3/4] CLEAN    (465.9s elapsed)  1776 (1972)
  [4/4] CLEAN    (510.4s elapsed)  1944 (2015)
  4 files in 510.4s = 0.0078 files/sec (28.2 files/hour)
```

- The number in `[N/M]` is which file completed (not which started)
- "X.Xs elapsed" is wall-clock time **since pass start**, not the file's own
  scan duration
- Files complete out of order — what you see is whatever finishes first

### Summary Table

```
 Workers   Files   Time(s)  Files/sec   Files/hr   Speedup
       1       4     744.3     0.0054       19.4     1.00x
       2       4     510.4     0.0078       28.2     1.46x
       4       4     485.3     0.0082       29.7     1.53x

Best throughput: 4 workers (0.0082 files/sec, 29.7 files/hour)
  1 -> 2 workers: +45.8%   ← good gain
  2 -> 4 workers:  +5.2%   ← diminishing returns
```

The annotations:

- **`← good gain`** = >20% improvement over previous worker count
- **`← diminishing returns`** = <10% improvement
- **No annotation** = 10-20% gain (judgment call)

### Picking the Right Answer

Look for the **lowest worker count that gets you most of the speedup**, not
necessarily the absolute fastest.

In the example above, the answer is **2 workers**, not 4:

- Going 1 → 2 buys +46% throughput (huge gain)
- Going 2 → 4 only buys +5% more (diminishing returns)
- 4 workers uses 2x the CPU/disk for negligible benefit
- 2 workers leaves room for other PC activity (browsing, video, etc.)

---

## Common Options

```powershell
# Smaller test (faster, less data)
.\RepairBrokenMedia.exe benchmark --limit 4 --workers 1,2,4

# Larger test (slower, more reliable averages)
.\RepairBrokenMedia.exe benchmark --limit 8 --workers 1,2,4,8

# Test a specific library (useful if mostly large files in one root)
.\RepairBrokenMedia.exe benchmark --root "Z:\Movies\T-Z"

# Skip the confirmation prompt (for automation)
.\RepairBrokenMedia.exe benchmark --yes

# Disable size filtering (test with whatever the library has)
.\RepairBrokenMedia.exe benchmark --max-file-gb 0

# Allow up to 5 GB files (the default is 3 GB to favor 1080p H.264)
.\RepairBrokenMedia.exe benchmark --max-file-gb 5
```

### Why the Size Cap?

The default is `--max-file-gb 3`, meaning the benchmark skips folders whose
largest video exceeds 3 GB. Reasons:

- **Throughput data from smaller files is just as valid** for picking
  `--workers` — the ratio between worker counts is what matters, not the
  absolute speed
- **4K HEVC files take 10-15 minutes per file** even at 1 worker, turning a
  2-minute benchmark into a 2-hour benchmark
- **The default keeps the test under ~30 minutes** for typical libraries

If you ONLY have huge files (e.g., dedicated 4K library), use `--max-file-gb 0`
or pick a different `--root`.

---

## Interpreting Edge Cases

### "Per-file time goes UP with more workers"

This is normal and expected. With 1 worker, a file gets all the resources
(CPU, NAS bandwidth, cache). With 2 workers, two files share — each
individual file finishes 20-50% slower than alone. The win is that **two are
running simultaneously**.

What matters is **total wall-clock per pass**, not per-file time.

### "2 workers is SLOWER than 1 (worse rate)"

This means you have a real bottleneck that contention makes worse:

- **HDD NAS** with random seeks costing more than parallel decode saves
- **Ancient/single-core CPU** that can't keep two ffmpeg processes fed
- **Antivirus scanning files as they're read** (huge fixed overhead per file
  read; multiplied by parallelism)

In this case, the right answer is `--workers 1`. Most modern PCs and SSD
NAS setups won't see this.

### "All worker counts show the same rate"

You're hitting a single hard bottleneck:

- Network link is saturated even with 1 worker (rare, only with 100 Mbit
  links)
- NAS service is single-threaded and serializes all requests (some old
  consumer NAS firmware)
- Antivirus or some middleware is the choke point

Try reducing the cap (`--max-file-gb 1`) to use smaller files; if the rate is
still flat, you're network/NAS-limited regardless of `--workers`.

### "First pass took an hour"

You probably hit a 4K HEVC file. CPU-bound HEVC null-decode runs at
~5-10 MB/s on a single core regardless of network speed. A 10 GB 4K HEVC
file = 15-30 minutes per file at 1 worker.

Cancel with Ctrl+C, then re-run with smaller files:

```powershell
.\RepairBrokenMedia.exe benchmark --max-file-gb 2 --limit 4 --workers 1,2
```

---

## Hardware-Based Recommendations

If you don't want to benchmark, these are reasonable starting points based on
your weakest link:

| Setup | Recommended `--workers` |
|---|---|
| 1 GbE network + SSD NAS + H.264 | 2-3 |
| 1 GbE network + SSD NAS + HEVC | 2 |
| 1 GbE network + HDD NAS + H.264 | 2 |
| 1 GbE network + HDD NAS + HEVC | 1-2 |
| 2.5 GbE + SSD NAS + H.264 | 4 |
| 2.5 GbE + SSD NAS + HEVC | 3-4 |
| 10 GbE + SSD NAS + H.264 | 6-8 |
| 10 GbE + SSD NAS + HEVC | 4 (CPU-bound) |
| 10 GbE + HDD NAS (any codec) | 1-2 (HDD seeks dominate) |

The benchmark will give you a more accurate number for your actual hardware.

---

## Estimating Library Scan Time

Once you know your throughput from the benchmark, estimate your full library:

```
hours = total_movies / files_per_hour
```

Examples from the real-world benchmark above (28.2 files/hour at 2 workers):

| Library size | Single-PC time | 3-PC parallel |
|---|---|---|
| 500 movies | ~18 hours | ~6 hours |
| 1,000 movies | ~36 hours | ~12 hours |
| 3,600 movies | ~5 days | ~2 days |

Multi-PC scaling is roughly linear because the Postgres-backed coordination
ensures each PC scans different folders (no duplicate work). See
[DEPLOYMENT.md](DEPLOYMENT.md) for multi-PC setup.

---

## What the Benchmark Does NOT Test

- **Long-tail file effects** — your sample is small. If your library has many
  outlier files (corrupt, weirdly encoded, etc.), real scans may have more
  variance.
- **Sustained throughput** — the benchmark runs for ~10-30 minutes. Multi-day
  scans may slow down if NAS caches fill, anti-virus throttles, or thermal
  throttling kicks in.
- **Multi-PC contention** — the benchmark runs alone. If 3 PCs hit the NAS
  simultaneously in production, throughput per PC will be lower than the
  single-PC benchmark suggests. Plan for ~70-80% of single-PC throughput per
  worker in multi-PC scenarios.
- **Remediation throughput** — the benchmark only measures scan speed, not
  the delete-and-Radarr-search workflow.

---

## When to Re-Benchmark

You don't need to benchmark often. Run it again if:

- You change network hardware (NIC, switch, NAS NIC, cabling)
- You add/replace NAS disks (especially HDD ↔ SSD swap)
- Your library composition shifts dramatically (e.g., bulk re-encode all
  movies to HEVC)
- You move the app to a different PC
- Throughput in normal scans seems significantly different from your
  benchmark numbers

The optimal `--workers` value should remain stable as long as your hardware
and library composition do.

---

## A Real Example

Here's the actual benchmark from the development PC (Intel X710 10 GbE NIC,
HDD-based NAS, 1080p HEVC library):

```
Discovering candidate folders...
Selecting 4 folder(s) with files <= 3.0 GB...

Will benchmark with 4 folder(s):
  - 10.0 Earthquake (2014)  (0.0 GB)
  - 16 Blocks (2006)        (1.1 GB)
  - 1776 (1972)             (1.9 GB)
  - 1944 (2015)             (1.7 GB)

Total per pass: 4.6 GB across 4 files
Number of passes: 3 (worker counts: [1, 2, 4])
Total data to read: ~14 GB
Rough time estimate: ~2-16 minutes

Ready to start? [y/N]: y

=== Pass with 1 worker(s) ===
  [1/4] CLEAN    (  2.7s elapsed)  10.0 Earthquake (2014)
  [2/4] CLEAN    (188.0s elapsed)  16 Blocks (2006)
  [3/4] CLEAN    (529.4s elapsed)  1776 (1972)
  [4/4] CLEAN    (744.3s elapsed)  1944 (2015)
  4 files in 744.3s = 0.0054 files/sec (19.4 files/hour)

=== Pass with 2 worker(s) ===
  [1/4] CLEAN    (  3.7s elapsed)  10.0 Earthquake (2014)
  [2/4] CLEAN    (241.4s elapsed)  16 Blocks (2006)
  [3/4] CLEAN    (465.9s elapsed)  1776 (1972)
  [4/4] CLEAN    (510.4s elapsed)  1944 (2015)
  4 files in 510.4s = 0.0078 files/sec (28.2 files/hour)

=== Pass with 4 worker(s) ===
  [1/4] CLEAN    (  X.Xs elapsed)  ...
  [4/4] CLEAN    (485.3s elapsed)  ...
  4 files in 485.3s = 0.0082 files/sec (29.7 files/hour)

============================================================
BENCHMARK SUMMARY
============================================================
 Workers   Files   Time(s)  Files/sec   Files/hr   Speedup
       1       4     744.3     0.0054       19.4     1.00x
       2       4     510.4     0.0078       28.2     1.46x
       4       4     485.3     0.0082       29.7     1.53x

Best throughput: 4 workers (0.0082 files/sec, 29.7 files/hour)
  1 -> 2 workers: +45.8%   ← good gain
  2 -> 4 workers:  +5.2%   ← diminishing returns
```

**Conclusion**: use `--workers 2`. Going to 4 only buys +5% throughput while
doubling resource pressure. The HDD NAS and HEVC codec combo means CPU and
disk seeks both contend, capping practical parallelism around 2.

---

*See also: [USERGUIDE.md](USERGUIDE.md) | [DEPLOYMENT.md](DEPLOYMENT.md) | [WORKFLOW.md](WORKFLOW.md) | [FUTURE.md](FUTURE.md)*
