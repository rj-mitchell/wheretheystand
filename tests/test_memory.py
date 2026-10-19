"""Streaming parse keeps memory bounded regardless of file size."""

import subprocess
import sys
import textwrap


def _write_file(path, n_speeches: int) -> int:
    para = "This is a long contribution about railway delays and agricultural zoning. " * 20
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<publicwhip scraperversion="a" latest="yes">\n')
        fh.write('<major-heading id="uk.org.publicwhip/debate/2025-01-01a.1.0" nospeaker="true">Big</major-heading>\n')
        for i in range(n_speeches):
            fh.write(f'<speech id="uk.org.publicwhip/debate/2025-01-01a.{i}.1" speakername="X" '
                     f'person_id="uk.org.publicwhip/person/10001" colnum="{i}" time="12:00:00" url="">'
                     f'<p>{para}</p><p>{para}</p></speech>\n')
        fh.write("</publicwhip>\n")
    return path.stat().st_size


def _peak_mb(path) -> tuple[int, int]:
    # VmHWM is this program's own peak. On Linux ru_maxrss survives exec, so it would
    # report the (much larger) pytest parent's peak instead.
    script = textwrap.dedent(f"""
        import resource
        from hansard.parse.xml_stream import parse_file
        n = sum(1 for _ in parse_file({str(path)!r}, "debates"))
        try:
            with open("/proc/self/status") as fh:
                peak = next(int(line.split()[1]) for line in fh if line.startswith("VmHWM:")) // 1024
        except OSError:
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 2**20  # macOS: bytes
        print(n, peak)
    """)
    out = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True).stdout.split()
    return int(out[0]), int(out[1])


def test_parse_memory_does_not_grow_with_file_size(tmp_path):
    (tmp_path / "debates").mkdir()
    small = tmp_path / "debates" / "debates2025-01-01a.xml"
    big = tmp_path / "debates" / "debates2025-01-02a.xml"
    _write_file(small, 2_000)
    size = _write_file(big, 80_000)
    assert size > 250_000_000
    n_small, peak_small = _peak_mb(small)
    n_big, peak_big = _peak_mb(big)
    assert (n_small, n_big) == (2_000, 80_000)
    # 40x more data, essentially the same peak: a DOM parse would need well over 1 GB here.
    assert peak_big - peak_small < 64, f"small {peak_small} MB vs big {peak_big} MB"
    assert peak_big < 400
