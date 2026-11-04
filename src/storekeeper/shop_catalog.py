"""Reviewed Shopify Skill + fixed read-only CLI adapter, never arbitrary shell."""
import hashlib
import json
import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

ROOT=Path(__file__).resolve().parents[2]
SKILL_DIRECTORY=ROOT/'skills/shop-catalog'
PACKAGE=ROOT/'var/vendor/shop-1.2.0/package'


class CatalogSearchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=200,description='Product search preserving customer intent, e.g. coffee maker. No invented preferences.')
    country:Literal['US','CA','GB']=Field(description='Known destination country, not inferred from language.')
    currency:Literal['USD','CAD','GBP','EUR']=Field(description='Known budget currency.')
    max_price_minor:int=Field(ge=1,le=10000000,description='Hard maximum price in minor units: USD100 is 10000, not 100.')
    limit:int=Field(default=3,ge=1,le=3)


def load_reviewed_skill() -> dict:
    manifest=json.loads((SKILL_DIRECTORY/'vendor/source.json').read_text(encoding='utf-8'))
    raw=(SKILL_DIRECTORY/'vendor/SHOP-SKILL.md').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['skill_sha256']:
        raise ValueError('Upstream skill snapshot differs from the reviewed version.')
    text=raw.decode('utf-8')
    sections=[]
    for start,end in [('## Help the user','### Connect Shop'),('### Search','### Stores')]:
        sections.append(text.split(start,1)[1].split(end,1)[0].strip())
    return {'ok':True,'name':'shop-catalog','upstream':manifest['source_repository'],
            'version':manifest['version'],'skill_sha256':manifest['skill_sha256'],
            'instructions':(SKILL_DIRECTORY/'SKILL.md').read_text(encoding='utf-8'),
            'reviewed_upstream_sections':sections,
            'scope':'Public catalog read-only. No sign-in, cart, checkout or private order access.'}


def invoke_official_cli(arguments:dict) -> dict:
    binary=PACKAGE/'bin/shop.mjs'
    if not binary.exists():
        raise FileNotFoundError('Run scripts/prepare_shop_skill.py first.')
    node=os.getenv('STOREKEEPER_NODE') or shutil.which('node')
    if not node:
        raise FileNotFoundError('Node.js is required. Set STOREKEEPER_NODE to the Node executable.')
    # Never send the model API key or use a user's existing Shop account store.
    env={key:value for key,value in os.environ.items() if key.upper() in
         ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','LOCALAPPDATA','COMSPEC')}
    directory=ROOT/'var/shop_anonymous'
    directory.mkdir(parents=True,exist_ok=True)
    env['SHOP_AUTH_FILE']=str(directory/'credentials.json')
    completed=subprocess.run([node,str(binary),'search','--arguments',json.dumps(arguments)],
        cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=35)
    if len(completed.stdout)>500000:
        raise ValueError('Catalog response too large.')
    document=json.loads(completed.stdout)
    if completed.returncode or not document.get('ok'):
        error=document.get('error') or {}
        return {'ok':False,'error_code':error.get('code','catalog_error'),
                'message':'Shop catalog search failed. No products may be invented.'}
    return document


def search_catalog(request:CatalogSearchRequest, *, runner=None) -> dict:
    arguments={'catalog':{'query':request.query,'context':{'address_country':request.country,'currency':request.currency},
                'filters':{'price':{'max':request.max_price_minor},'ships_to':{'country':request.country},'available':True},
                'pagination':{'limit':request.limit}}}
    raw=(runner or invoke_official_cli)(arguments)
    if not raw.get('ok'):
        return raw
    data=raw.get('data',{})
    offers=[]
    rejected=0
    for product in data.get('products',[]):
        for variant in product.get('variants',[])[:1]:
            price=variant.get('price') or {}
            amount=price.get('amount')
            url=variant.get('url') or variant.get('checkout_url') or product.get('url')
            available=(variant.get('availability') or {}).get('available')
            # Validate returned facts; search hints alone do not prove constraints.
            if (type(amount) is not int or amount<0 or price.get('currency')!=request.currency
                    or amount>request.max_price_minor or available is not True
                    or not isinstance(url,str) or not url.startswith('https://')):
                rejected+=1
                continue
            offers.append({'product_id':product.get('id'),'title':product.get('title'),
                'variant_id':variant.get('id'),'price_minor':amount,
                'price':str((Decimal(amount)/100).quantize(Decimal('0.01'))),'currency':price['currency'],
                'available':available,'options':variant.get('options',[]),
                'seller':variant.get('seller',{}),'url':url})
    return {'ok':True,'source':'Shopify Shop MCP via official @shopify/shop CLI',
            'scope':'Public cross-merchant catalog; not the demo store private inventory.',
            'request':arguments,'offers':offers,'rejected_offers':rejected,
            'messages':data.get('messages',[]),
            'note':'Availability is for returned variants at query time. No stock quantity or arrival-date guarantee.'}


def make_catalog_tools(*, runner=None):
    loaded=False

    @tool
    def load_shop_skill() -> str:
        """Load the reviewed read-only Shopify Shop Skill instructions before searching public products. No login or checkout capabilities."""
        nonlocal loaded
        payload=load_reviewed_skill()
        loaded=True
        return json.dumps(payload,ensure_ascii=False)

    @tool(args_schema=CatalogSearchRequest)
    def search_shop_catalog(query,country,currency,max_price_minor,limit=3) -> str:
        """Search real public merchant products through Shopify's official Shop CLI/MCP. Requires load_shop_skill first; returns prices, sellers, selected availability and source URLs. Read-only."""
        if not loaded:
            raise PermissionError('Load the reviewed shop skill before searching.')
        request=CatalogSearchRequest(query=query,country=country,currency=currency,max_price_minor=max_price_minor,limit=limit)
        return json.dumps(search_catalog(request,runner=runner),ensure_ascii=False)

    return [load_shop_skill,search_shop_catalog]
