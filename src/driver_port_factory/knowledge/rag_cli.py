"""Controller-owned vector preparation and worker read-only RAG retrieval commands."""

from .contracts import KnowledgeDomain


def register(commands):
    parser = commands.add_parser("rag", help="retrieve bounded cited originals for generation")
    parser.add_argument("path")
    parser.add_argument("--query", required=True)
    parser.add_argument("--mode", choices=("auto", "bm25", "hybrid"), default="auto")
    parser.add_argument("--domain", type=KnowledgeDomain, choices=list(KnowledgeDomain))
    parser.add_argument("--record-id")
    parser.add_argument("--path-prefix")
    parser.add_argument("--budget", type=int, default=12000)
    parser.add_argument("--limit", type=int, default=5)
    parser.set_defaults(handler=retrieve)
    parser = commands.add_parser("embed", help="explicitly build vectors with a local model")
    parser.add_argument("path")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--query-prefix", default="")
    parser.add_argument("--document-prefix", default="")
    parser.set_defaults(handler=build)


def retrieve(args):
    import json

    from ..short_refs import emit
    from .cli import _query_index
    from .rag import query

    def encode(value):
        return json.dumps(emit(value), ensure_ascii=False)

    print(
        encode(
            query(
                _query_index(args),
                args.query,
                mode=args.mode,
                budget=args.budget,
                limit=args.limit,
                domain=args.domain,
                record_id=args.record_id,
                path_prefix=args.path_prefix,
            )
        )
    )


def build(args):
    import json

    from .cli import _query_index
    from .embeddings import LocalEncoder
    from .embeddings import build as build_vectors

    encoder = LocalEncoder(
        args.model_path, query_prefix=args.query_prefix, document_prefix=args.document_prefix
    )
    print(json.dumps(build_vectors(_query_index(args), encoder), ensure_ascii=False))
