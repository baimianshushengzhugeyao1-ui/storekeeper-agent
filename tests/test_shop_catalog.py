import unittest
from unittest.mock import patch
from storekeeper.shop_catalog import CatalogSearchRequest, search_catalog, make_catalog_tools, load_reviewed_skill
from storekeeper.tool_agent import run_tool_agent
from langchain_core.messages import AIMessage


def catalog_document():
    def product(amount=4995,currency='USD',available=True):
        return {'id':'p1','title':'Coffee maker','variants':[{'id':'v1','price':{'amount':amount,'currency':currency},
            'availability':{'available':available},'seller':{'name':'Example seller'},
            'url':'https://example.com/product?variant=1','options':[{'name':'Color','label':'Black'}]}]}
    return {'ok':True,'data':{'products':[product(),product(12000),product(currency='EUR'),product(available=False)],'messages':[]}}


class CatalogSkillTests(unittest.TestCase):
    def test_loads_reviewed_official_version_and_not_checkout_section(self):
        payload=load_reviewed_skill()
        self.assertEqual(payload['version'],'1.2.0')
        self.assertEqual(payload['upstream'],'https://github.com/Shopify/shop')
        self.assertNotIn('### Cart and checkout',''.join(payload['reviewed_upstream_sections']))

    def test_filters_returned_price_currency_and_availability(self):
        captured=[]
        def runner(args):
            captured.append(args)
            return catalog_document()
        result=search_catalog(CatalogSearchRequest(query='coffee maker',country='US',currency='USD',max_price_minor=10000,limit=2),runner=runner)
        self.assertEqual(len(result['offers']),1)
        self.assertEqual(result['offers'][0]['price'],'49.95')
        self.assertEqual(result['rejected_offers'],3)
        self.assertEqual(captured[0]['catalog']['filters']['price']['max'],10000)
        self.assertEqual(captured[0]['catalog']['filters']['ships_to']['country'],'US')

    def test_skill_must_be_loaded_before_external_call(self):
        calls=[]
        tools={t.name:t for t in make_catalog_tools(runner=lambda p:calls.append(p))}
        args=dict(query='coffee maker',country='US',currency='USD',max_price_minor=10000)
        with self.assertRaises(PermissionError):
            tools['search_shop_catalog'].invoke(args)
        self.assertEqual(calls,[])

    def test_unknown_purchase_arguments_are_not_accepted(self):
        tools={t.name:t for t in make_catalog_tools(runner=lambda p:catalog_document())}
        self.assertEqual(set(tools),{'load_shop_skill','search_shop_catalog'})
        tools['load_shop_skill'].invoke({})
        with self.assertRaises(ValueError):
            tools['search_shop_catalog'].invoke(dict(query='coffee maker',country='US',currency='USD',max_price_minor=10000,checkout=True))

    def test_model_load_search_result_round_trip(self):
        class Model:
            turns=0
            def bind_tools(inner,tools,**kwargs):
                return inner
            def invoke(inner,messages):
                inner.turns+=1
                if inner.turns==1:
                    return AIMessage(content='',tool_calls=[{'name':'load_shop_skill','args':{},'id':'skill'}])
                if inner.turns==2:
                    return AIMessage(content='',tool_calls=[{'name':'search_shop_catalog','args':dict(query='coffee maker',country='US',currency='USD',max_price_minor=10000),'id':'catalog'}])
                return AIMessage(content='Returned example product.')
        result=run_tool_agent('Find a coffee maker under USD100 for US.',model=Model(),toolset='catalog',catalog_runner=lambda p:catalog_document())
        self.assertFalse(result['had_error'])
        self.assertEqual(result['tool_calls'],2)
        self.assertEqual(result['sources'],['https://example.com/product?variant=1'])


if __name__=='__main__':
    unittest.main()
