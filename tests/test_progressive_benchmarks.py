from scripts.benchmark_progressive_analysis import corpus_game
from src.progressive_prepare import replay


def test_declared_strata_and_exact_context_distinctions():
    a,_,_=replay(corpus_game(0));b,_,_=replay(corpus_game(3))
    assert [x['key'] for x in a]==[x['key'] for x in b]
    a,_,_=replay(corpus_game(1));b,_,_=replay(corpus_game(4))
    assert a[4]['request']['fen']==b[4]['request']['fen']
    assert a[4]['key']!=b[4]['key']
    assert corpus_game(2)==corpus_game(2)
