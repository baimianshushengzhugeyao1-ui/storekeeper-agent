"""Fetch pinned official Shopify Shop CLI/Skill without executing install scripts."""
import hashlib
import io
import json
import tarfile
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
VERSION='1.2.0'
URL=f'https://registry.npmjs.org/@shopify/shop/-/shop-{VERSION}.tgz'
EXPECTED_ARCHIVE_SHA256='40e7df83ade1e44c9a054483a6f905ad76d4d16ba2546d0a680b0153456eec75'


def main():
    response=requests.get(URL,timeout=30)
    response.raise_for_status()
    if len(response.content)>10_000_000:
        raise ValueError('Unexpected archive size.')
    if hashlib.sha256(response.content).hexdigest()!=EXPECTED_ARCHIVE_SHA256:
        raise ValueError('Official package differs from the reviewed pinned archive.')
    destination=ROOT/'var/vendor'/f'shop-{VERSION}'
    destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(response.content),mode='r:gz') as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            target=(destination/member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Archive path escapes target.')
            target.parent.mkdir(parents=True,exist_ok=True)
            data=archive.extractfile(member).read()
            target.write_bytes(data)
    package=destination/'package'
    metadata=json.loads((package/'package.json').read_text(encoding='utf-8'))
    assert metadata['name']=='@shopify/shop' and metadata['version']==VERSION
    skill=(package/'skills/shop/SKILL.md').read_bytes()
    vendor=ROOT/'skills/shop-catalog/vendor'
    vendor.mkdir(parents=True,exist_ok=True)
    (vendor/'SHOP-SKILL.md').write_bytes(skill)
    (vendor/'LICENSE').write_bytes((package/'LICENSE').read_bytes())
    manifest={'package':'@shopify/shop','version':VERSION,'url':URL,
              'source_repository':'https://github.com/Shopify/shop',
              'archive_sha256':hashlib.sha256(response.content).hexdigest(),
              'skill_sha256':hashlib.sha256(skill).hexdigest(),
              'scope':'Official skill snapshot; project executes only public catalog search. No login/cart/checkout/order tools.'}
    (vendor/'source.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest),flush=True)


if __name__=='__main__':
    main()
