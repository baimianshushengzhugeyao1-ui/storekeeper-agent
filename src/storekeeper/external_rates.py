"""Read-only Frankfurter adapter. Fixed host, validated pair, decimal math."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

import requests
from pydantic import BaseModel, ConfigDict, Field

Currency=Literal['USD','EUR','CNY','GBP','JPY']


class ConversionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    amount: Decimal=Field(gt=0,le=1000000,max_digits=12,decimal_places=2,
                          description='Positive amount to convert, at most two decimal places.')
    base_currency: Currency=Field(description='Source currency ISO code, uppercase.')
    quote_currency: Currency=Field(description='Target currency ISO code, uppercase.')


class RateResponse(BaseModel):
    date: date
    base: str
    quote: str
    rate: Decimal=Field(gt=0,le=1000000000)


def convert_currency(request: ConversionRequest, *, session=None) -> dict:
    if request.base_currency==request.quote_currency:
        return {'ok':True,'amount':str(request.amount),'base':request.base_currency,
                'quote':request.quote_currency,'rate':'1','converted_amount':str(request.amount),
                'source':'identity conversion','as_of':None}
    url=f'https://api.frankfurter.dev/v2/rate/{request.base_currency.lower()}/{request.quote_currency.lower()}'
    client=session or requests
    response=client.get(url,timeout=(5,15),allow_redirects=False,
                        headers={'Accept':'application/json','User-Agent':'Storekeeper-ReadOnly-Agent'})
    response.raise_for_status()
    if response.status_code!=200:
        raise ValueError('Unexpected rate service status.')
    payload=RateResponse.model_validate(response.json())
    if payload.base!=request.base_currency or payload.quote!=request.quote_currency:
        raise ValueError('Rate service returned a different currency pair.')
    converted=(request.amount*payload.rate).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    return {'ok':True,'amount':str(request.amount),'base':payload.base,'quote':payload.quote,
            'rate':str(payload.rate),'converted_amount':str(converted),'as_of':payload.date.isoformat(),
            'source':url,'note':'Indicative reference conversion, not a bank settlement or refund quote.'}
