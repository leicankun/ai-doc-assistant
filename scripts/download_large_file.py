"""Resumable range downloader for large public model/wheel files; verifies SHA256."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import time
from urllib.request import Request, urlopen


def download(url, destination, size, sha256, workers=8):
    destination = Path(destination)
    if destination.exists() and destination.stat().st_size == size:
        with destination.open('rb') as source:
            verified = hashlib.file_digest(source, 'sha256').hexdigest() == sha256
        if verified:
            print(f'Already verified: {destination}', flush=True)
            return
    part_dir = destination.parent / (destination.name + '.parts')
    part_dir.mkdir(parents=True, exist_ok=True)
    chunk_size = 2 * 1024 * 1024
    count = (size + chunk_size - 1) // chunk_size

    def fetch(index):
        start = index * chunk_size
        end = min(size, start + chunk_size) - 1
        path = part_dir / str(index)
        if path.exists() and path.stat().st_size == end - start + 1:
            return end - start + 1
        for attempt in range(8):
            try:
                request = Request(url, headers={'Range': f'bytes={start}-{end}'})
                with urlopen(request, timeout=40) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                        raise ValueError('Server did not honor the requested range')
                    data = response.read()
                if len(data) != end - start + 1:
                    raise ValueError('Incomplete range response')
                path.write_bytes(data)
                return len(data)
            except Exception as exc:
                if attempt == 7:
                    raise RuntimeError(f'Range {index} failed: {type(exc).__name__}') from None
                time.sleep(min(5, attempt + 1))

    done = 0
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, index) for index in range(count)]
        for number, future in enumerate(as_completed(futures), 1):
            done += future.result()
            if number % 16 == 0 or number == count:
                elapsed = time.perf_counter() - started
                print(f'{destination.name}: {done / 1024**2:.0f}/{size / 1024**2:.0f} MiB '
                      f'({done / max(elapsed, .001) / 1024**2:.2f} MiB/s)', flush=True)
    temporary = destination.with_name(destination.name + '.assembling')
    digest = hashlib.sha256()
    with temporary.open('wb') as output:
        for index in range(count):
            with (part_dir / str(index)).open('rb') as source:
                data = source.read()
            digest.update(data)
            output.write(data)
    if digest.hexdigest() != sha256:
        raise RuntimeError('SHA256 verification failed; destination was not replaced')
    temporary.replace(destination)
    print(f'SHA256 verified: {destination}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--size', required=True, type=int)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    download(args.url, args.output, args.size, args.sha256, args.workers)
