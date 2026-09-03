#!/usr/bin/env python3
"""Inject data/gci_data.json into the portal page between the GCI_DATA markers.

Usage: python3 build_page.py <path-to-doom-gauge.html>
Idempotent: re-running replaces the previous injection.
"""
import json, re, sys

def main():
    page_path = sys.argv[1]
    data_path = __file__.rsplit('/', 1)[0] + '/data/gci_data.json'
    with open(data_path) as f:
        data = json.load(f)
    with open(page_path) as f:
        html = f.read()
    blob = json.dumps(data, separators=(',', ':')).replace('</', '<\\/')
    new = re.sub(r'/\*GCI_DATA\*/.*?/\*END_GCI_DATA\*/',
                 lambda m: '/*GCI_DATA*/' + blob + '/*END_GCI_DATA*/',
                 html, count=1, flags=re.S)
    if new == html and '/*GCI_DATA*/' not in html:
        sys.exit('markers not found in page')
    with open(page_path, 'w') as f:
        f.write(new)
    print(f"injected: GCI {data['gci']} ({data['band']}) as of {data['asof']}, "
          f"{len(data['history'])} months history, {len(data['movers'])} movers")

if __name__ == '__main__':
    main()
