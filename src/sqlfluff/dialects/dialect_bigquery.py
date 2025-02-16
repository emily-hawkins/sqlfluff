"""The BigQuery dialect.

This inherits from the ansi dialect, with changes as specified by
https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax
and
https://cloud.google.com/bigquery/docs/reference/standard-sql/lexical#string_and_bytes_literals
"""

import itertools

from sqlfluff.core.parser import (
    Anything,
    BaseSegment,
    NamedSegment,
    OneOf,
    Ref,
    Sequence,
    Bracketed,
    Delimited,
    ReSegment,
    AnyNumberOf,
    KeywordSegment,
    Indent,
)

from sqlfluff.core.dialects import load_raw_dialect

ansi_dialect = load_raw_dialect("ansi")
bigquery_dialect = ansi_dialect.copy_as("bigquery")

bigquery_dialect.patch_lexer_struct(
    [
        # Quoted literals can have r or b (case insensitive) prefixes, in any order, to
        # indicate a raw/regex string or byte sequence, respectively.  Allow escaped quote
        # characters inside strings by allowing \" with an optional even multiple of
        # backslashes in front of it.
        # https://cloud.google.com/bigquery/docs/reference/standard-sql/lexical#string_and_bytes_literals
        # Triple quoted variant first, then single quoted
        (
            "single_quote",
            "regex",
            r"([rR]?[bB]?|[bB]?[rR]?)?('''((?<!\\)(\\{2})*\\'|'{,2}(?!')|[^'])*(?<!\\)(\\{2})*'''|'((?<!\\)(\\{2})*\\'|[^'])*(?<!\\)(\\{2})*')",
            dict(is_code=True),
        ),
        (
            "double_quote",
            "regex",
            r'([rR]?[bB]?|[bB]?[rR]?)?(\"\"\"((?<!\\)(\\{2})*\\\"|\"{,2}(?!\")|[^\"])*(?<!\\)(\\{2})*\"\"\"|"((?<!\\)(\\{2})*\\"|[^"])*(?<!\\)(\\{2})*")',
            dict(is_code=True),
        ),
    ]
)

bigquery_dialect.add(
    DoubleQuotedLiteralSegment=NamedSegment.make(
        "double_quote", name="quoted_literal", type="literal", trim_chars=('"',)
    ),
    StructKeywordSegment=KeywordSegment.make("struct", name="struct"),
)


bigquery_dialect.replace(
    FunctionContentsExpressionGrammar=OneOf(
        Ref("DatetimeUnitSegment"),
        Sequence(
            Ref("ExpressionSegment"),
            Sequence(OneOf("IGNORE", "RESPECT"), "NULLS", optional=True),
        ),
    ),
)


# Add additional datetime units
# https://cloud.google.com/bigquery/docs/reference/standard-sql/date_functions#extract
bigquery_dialect.sets("datetime_units").update(
    ["MICROSECOND", "DAYOFWEEK", "ISOWEEK", "ISOYEAR", "DATE", "DATETIME", "TIME"]
)

# Unreserved Keywords
bigquery_dialect.sets("unreserved_keywords").add("SYSTEM_TIME")
bigquery_dialect.sets("unreserved_keywords").remove("FOR")
bigquery_dialect.sets("unreserved_keywords").add("STRUCT")
# Reserved Keywords
bigquery_dialect.sets("reserved_keywords").add("FOR")

# In BigQuery, UNNEST() returns a "value table".
# https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax#value_tables
bigquery_dialect.sets("value_table_functions").update(["unnest"])

# Bracket pairs (a set of tuples)
bigquery_dialect.sets("bracket_pairs").update(
    [
        # NB: Angle brackets can be mistaken, so False
        ("angle", "LessThanSegment", "GreaterThanSegment", False)
    ]
)


# BigQuery allows functions in INTERVAL
@bigquery_dialect.segment(replace=True)
class IntervalExpressionSegment(BaseSegment):
    """An interval with a function as value segment."""

    type = "interval_expression"
    match_grammar = Sequence(
        "INTERVAL",
        OneOf(
            Ref("NumericLiteralSegment"),
            Ref("QuotedLiteralSegment"),
            Ref("FunctionSegment"),
        ),
        OneOf(Ref("QuotedLiteralSegment"), Ref("DatetimeUnitSegment")),
    )


@bigquery_dialect.segment(replace=True)
class SelectClauseSegment(ansi_dialect.get_segment("SelectClauseSegment")):  # type: ignore
    """In BigQuery, select * as struct is valid."""

    parse_grammar = Sequence(
        "SELECT",
        Ref("SelectClauseModifierSegment", optional=True),
        Indent,
        OneOf(
            Sequence(
                "AS",
                "STRUCT",
                Ref("StarSegment"),
                Ref("StarModifierSegment", optional=True),
            ),
            Delimited(
                Ref("SelectClauseElementSegment"),
                delimiter=Ref("CommaSegment"),
                allow_trailing=True,
            ),
        ),
    )


@bigquery_dialect.segment(replace=True)
class SelectClauseElementSegment(
    ansi_dialect.get_segment("SelectClauseElementSegment")  # type: ignore
):
    """BigQuery also supports the special "Struct" construct."""

    parse_grammar = OneOf(
        # *, blah.*, blah.blah.*, etc.
        Ref("WildcardExpressionSegment"),
        Sequence(
            OneOf(
                Ref("LiteralGrammar"),
                Ref("BareFunctionSegment"),
                Ref("FunctionSegment"),
                Ref("IntervalExpressionSegment"),
                Ref("TypelessStructSegment"),
                Ref("ColumnReferenceSegment"),
                Ref("ExpressionSegment"),
            ),
            Ref("AliasExpressionSegment", optional=True),
        ),
    )


bigquery_dialect.replace(
    QuotedIdentifierSegment=NamedSegment.make(
        "back_quote", name="quoted_identifier", type="identifier", trim_chars=("`",)
    ),
    # Add two elements to the ansi LiteralGrammar
    LiteralGrammar=ansi_dialect.get_grammar("LiteralGrammar").copy(
        insert=[Ref("DoubleQuotedLiteralSegment"), Ref("LiteralCoercionSegment")]
    ),
    PostTableExpressionGrammar=Sequence(
        Sequence(
            "FOR", "SYSTEM_TIME", "AS", "OF", Ref("ExpressionSegment"), optional=True
        ),
        Sequence("WITH", "OFFSET", "AS", Ref("SingleIdentifierGrammar"), optional=True),
    ),
    FunctionNameSegment=ReSegment.make(
        # In BigQuery struct() has a special syntax, so we don't treat it as a function
        r"[A-Z][A-Z0-9_]*",
        name="function_name",
        type="function_name",
        _anti_template=r"STRUCT",
    ),
)


@bigquery_dialect.segment(replace=True)
class FunctionDefinitionGrammar(BaseSegment):
    """This is the body of a `CREATE FUNCTION AS` statement."""

    match_grammar = Sequence(
        AnyNumberOf(
            Sequence(
                "LANGUAGE",
                # Not really a parameter, but best fit for now.
                Ref("ParameterNameSegment"),
                Sequence(
                    "OPTIONS",
                    Bracketed(
                        Delimited(
                            Sequence(
                                Ref("ParameterNameSegment"),
                                Ref("EqualsSegment"),
                                Anything(),
                            ),
                            delimiter=Ref("CommaSegment"),
                        )
                    ),
                    optional=True,
                ),
            ),
            # There is some syntax not implemented here,
            Sequence(
                "AS",
                OneOf(
                    Ref("DoubleQuotedLiteralSegment"),
                    Ref("QuotedLiteralSegment"),
                    Bracketed(
                        OneOf(Ref("ExpressionSegment"), Ref("SelectStatementSegment"))
                    ),
                ),
            ),
        )
    )


@bigquery_dialect.segment(replace=True)
class WildcardExpressionSegment(BaseSegment):
    """An extension of the star expression for Bigquery."""

    type = "wildcard_expression"
    match_grammar = ansi_dialect.get_segment(
        "WildcardExpressionSegment"
    ).match_grammar.copy(
        insert=[
            # Optional EXCEPT or REPLACE clause
            # https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax#select_replace
            Ref("ExceptClauseSegment", optional=True),
            Ref("ReplaceClauseSegment", optional=True),
        ]
    )


@bigquery_dialect.segment()
class ExceptClauseSegment(BaseSegment):
    """SELECT EXCEPT clause."""

    type = "select_except_clause"
    match_grammar = Sequence(
        "EXCEPT",
        Bracketed(
            Delimited(Ref("SingleIdentifierGrammar"), delimiter=Ref("CommaSegment"))
        ),
    )


@bigquery_dialect.segment()
class ReplaceClauseSegment(BaseSegment):
    """SELECT REPLACE clause."""

    type = "select_replace_clause"
    match_grammar = Sequence(
        "REPLACE",
        OneOf(
            # Multiple replace in brackets
            Bracketed(
                Delimited(
                    # Not *really* a select target element. It behaves exactly
                    # the same way however.
                    Ref("SelectClauseElementSegment"),
                    delimiter=Ref("CommaSegment"),
                )
            ),
            # Single replace not in brackets.
            Ref("SelectClauseElementSegment"),
        ),
    )


@bigquery_dialect.segment(replace=True)
class DatatypeSegment(BaseSegment):
    """A data type segment.

    In particular here, this enabled the support for
    the STRUCT datatypes.
    """

    type = "data_type"
    match_grammar = OneOf(  # Parameter type
        Ref("DatatypeIdentifierSegment"),  # Simple type
        Sequence("ANY", "TYPE"),  # SQL UDFs can specify this "type"
        Sequence("ARRAY", Bracketed(Ref("DatatypeSegment"), bracket_type="angle")),
        Sequence(
            "STRUCT",
            Bracketed(
                Delimited(  # Comma-separated list of field names/types
                    Sequence(Ref("ParameterNameSegment"), Ref("DatatypeSegment")),
                    delimiter=Ref("CommaSegment"),
                ),
                bracket_type="angle",
            ),
        ),
    )


@bigquery_dialect.segment()
class TypelessStructSegment(BaseSegment):
    """Expression to construct a STRUCT with implicit types.

    https://cloud.google.com/bigquery/docs/reference/standard-sql/data-types#typeless_struct_syntax
    """

    type = "typeless_struct"
    match_grammar = Sequence(
        "STRUCT",
        Bracketed(
            Delimited(
                AnyNumberOf(
                    Sequence(
                        OneOf(
                            Ref("LiteralGrammar"),
                            Ref("FunctionSegment"),
                            Ref("IntervalExpressionSegment"),
                            Ref("ObjectReferenceSegment"),
                            Ref("ExpressionSegment"),
                        ),
                        Ref("AliasExpressionSegment", optional=True),
                    ),
                ),
                delimiter=Ref("CommaSegment"),
            ),
            optional=True,
        ),
    )


@bigquery_dialect.segment()
class LiteralCoercionSegment(BaseSegment):
    """A casting operation with a type name preceding a string literal.

    BigQuery allows string literals to be explicitly coerced to one of the
    following 4 types:
    - DATE
    - DATETIME
    - TIME
    - TIMESTAMP

    https://cloud.google.com/bigquery/docs/reference/standard-sql/conversion_rules#literal_coercion

    """

    type = "cast_expression"
    match_grammar = Sequence(
        OneOf("DATE", "DATETIME", "TIME", "TIMESTAMP"),
        Ref("QuotedLiteralSegment"),
    )


@bigquery_dialect.segment()
class HyphenatedObjectReferenceSegment(ansi_dialect.get_segment("ObjectReferenceSegment")):  # type: ignore
    """A reference to an object that may contain embedded hyphens."""

    type = "hyphenated_object_reference"
    match_grammar = ansi_dialect.get_segment(
        "ObjectReferenceSegment"
    ).match_grammar.copy()
    match_grammar.delimiter = OneOf(
        Ref("DotSegment"),
        Sequence(Ref("DotSegment"), Ref("DotSegment")),
        Sequence(Ref("MinusSegment")),
    )

    def iter_raw_references(self):
        """Generate a list of reference strings and elements.

        Each reference is an ObjectReferencePart. Overrides the base class
        because hyphens (MinusSegment) causes one logical part of the name to
        be split across multiple elements, e.g. "table-a" is parsed as three
        segments.
        """
        # For each descendant element, group them, using "dot" elements as a
        # delimiter.
        for is_dot, elems in itertools.groupby(
            self.recursive_crawl("identifier", "binary_operator", "dot"),
            lambda e: e.type == "dot",
        ):
            if not is_dot:
                segments = list(elems)
                parts = [seg.raw_trimmed() for seg in segments]
                yield self.ObjectReferencePart("".join(parts), segments)


@bigquery_dialect.segment(replace=True)
class TableExpressionSegment(BaseSegment):
    """Main table expression e.g. within a FROM clause, with hyphen support."""

    type = "table_expression"
    match_grammar = ansi_dialect.get_segment(
        "TableExpressionSegment"
    ).match_grammar.copy(
        insert=[
            Ref("HyphenatedObjectReferenceSegment"),
        ]
    )
