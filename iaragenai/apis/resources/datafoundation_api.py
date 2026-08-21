"""Compat minimal para iaragenai.apis.resources.datafoundation_api.types

Define `types` contendo referências de classes usadas no projeto.
"""

class SimilaritySearchKnowledgeBaseVersionReference:
    def __init__(self, *args, **kwargs):
        pass


class KnowledgeBaseVersionReference:
    def __init__(self, *args, **kwargs):
        pass


class SimilaritySearchKnowledgeBaseReference:
    def __init__(self, *args, **kwargs):
        pass


class KnowledgeBaseReference:
    def __init__(self, *args, **kwargs):
        pass


class _TypesModule:
    SimilaritySearchKnowledgeBaseVersionReference = SimilaritySearchKnowledgeBaseVersionReference
    KnowledgeBaseVersionReference = KnowledgeBaseVersionReference
    SimilaritySearchKnowledgeBaseReference = SimilaritySearchKnowledgeBaseReference
    KnowledgeBaseReference = KnowledgeBaseReference


types = _TypesModule()
