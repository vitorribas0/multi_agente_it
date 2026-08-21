import sys, traceback, os

sys.path.insert(0, 'repo-restaurado')
# Avoid CA overrides for this diagnostic run
os.environ.pop('REQUESTS_CA_BUNDLE', None)
os.environ.pop('CURL_CA_BUNDLE', None)

try:
    from iaragenai import IaraGenAI
    c = IaraGenAI()
    models = c.models.list()
    print('models.list ->', type(models), models)
except Exception:
    traceback.print_exc()
