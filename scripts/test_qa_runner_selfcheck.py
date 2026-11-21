"""Test runner's JUnit parser against known synthetic records, no target repo needed."""
import importlib.util
from pathlib import Path
import tempfile
from xml.etree.ElementTree import Element, SubElement, ElementTree

p=Path(__file__).with_name('run_independent_qa.py')
spec=importlib.util.spec_from_file_location('run_independent_qa',p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory() as d:
    root=Element('testsuite')
    x=SubElement(root,'testcase',name='test_R01_sample'); SubElement(x,'failure',message='wrong NAV')
    y=SubElement(root,'testcase',name='test_R02_sample')
    z=SubElement(root,'testcase',name='test_R03_sample'); SubElement(z,'skipped')
    loc=Path(d)/'sample.xml';ElementTree(root).write(loc)
    r=mod.parse_junit(loc)
    assert (r['total'],r['failed'],r['passed'],r['skipped'],r['errors'])==(3,1,1,1,0),r
    assert r['cases'][0]['details']=='wrong NAV',r
print('QA runner JUnit selfcheck: PASS')
