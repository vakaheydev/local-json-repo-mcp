from pathlib import Path

from json_repository import (
    find_by_any_json_value,
    find_by_json_array_value,
    find_by_json_string,
    find_by_json_value,
    find_files,
    find_json_files,
    find_string_anywhere_in_json,
    find_text_in_files,
    get_file,
    get_json,
    get_json_value,
    json_path_exists,
    select_json_fields,
    summarize_json_documents,
)


# Change this to the repository you want to inspect.
REPO = Path(r"C:\path\to\target-repository")


# 1. Find all JSON files
json_files = find_json_files(REPO)
print(json_files)


# 2. Find files by one glob mask
api_files = find_json_files(
    REPO,
    "**/*.Api.json",
)
print(api_files)


# 3. Find files by several masks
multi_env_files = find_json_files(
    REPO,
    [
        "test/**/*.Api.json",
        "regress/**/*.Api.json",
        "prod/**/*.Api.json",
    ],
)
print(multi_env_files)


# 4. Exclude directories while searching
filtered_files = find_files(
    REPO,
    "**/*.json",
    exclude_patterns=[
        "**/.git/**",
        "**/node_modules/**",
    ],
)
print(filtered_files)


# 5. Read a text file
readme = get_file(REPO, "README.md")
print(readme)


# 6. Read and parse a JSON file
api = get_json(
    REPO,
    "prod/example.Api.json",
)
print(api)


# 7. Read a nested JSON value using dot notation
api_id = get_json_value(api, "metadata.id")
print(api_id)

# List indexes are supported too.
first_server_url = get_json_value(
    api,
    "servers.0.url",
)
print(first_server_url)


# 8. Check whether a JSON path exists
if json_path_exists(api, "proxy.virtual_hosts"):
    print("virtual_hosts exists")


# 9. Find JSON documents by an exact value
by_id = find_by_json_value(
    REPO,
    json_path="id",
    expected_value="abc-123",
    patterns="**/*.Api.json",
)
print(by_id)


# 10. Find JSON documents by an exact nested value
by_nested_id = find_by_json_value(
    REPO,
    json_path="metadata.id",
    expected_value="abc-123",
    patterns="**/*.json",
)
print(by_nested_id)


# 11. Partial string match in a specific JSON field
by_name = find_by_json_string(
    REPO,
    json_path="name",
    query="payment",
    patterns="**/*.Api.json",
    case_sensitive=False,
)
print(by_name)


# 12. Search for a string anywhere inside JSON values
path_matches = find_string_anywhere_in_json(
    REPO,
    query="/payments/v1",
    patterns="**/*.Api.json",
)
print(path_matches)


# 13. Search for an exact value anywhere in JSON
jwt_matches = find_by_any_json_value(
    REPO,
    expected_value="jwt",
    patterns="**/*.Api.json",
)
print(jwt_matches)


# 14. Find JSON documents whose array contains a value
internal_apis = find_by_json_array_value(
    REPO,
    json_path="tags",
    expected_value="internal",
    patterns="**/*.Api.json",
)
print(internal_apis)


# 15. Raw text search in arbitrary files
text_matches = find_text_in_files(
    REPO,
    query="payments",
    patterns=[
        "**/*.json",
        "**/*.yaml",
        "**/*.yml",
    ],
    case_sensitive=False,
    max_matches_per_file=10,
)
print(text_matches)


# 16. Extract only selected fields from one JSON document
summary = select_json_fields(
    api,
    fields=[
        "id",
        "name",
        "version",
        "proxy.virtual_hosts",
    ],
)
print(summary)


# 17. Convert search results into compact MCP-friendly responses
compact_results = summarize_json_documents(
    by_name,
    fields=[
        "id",
        "name",
        "version",
    ],
)
print(compact_results)


# Typical MCP usage pattern:
#
# @mcp.tool()
# def find_api_by_name(name: str) -> list[dict]:
#     documents = find_by_json_string(
#         REPO,
#         json_path="name",
#         query=name,
#         patterns="**/*.Api.json",
#     )
#
#     return summarize_json_documents(
#         documents,
#         fields=["id", "name", "version"],
#     )
#
#
# @mcp.tool()
# def get_api(file_path: str) -> dict:
#     return get_json(REPO, file_path)
