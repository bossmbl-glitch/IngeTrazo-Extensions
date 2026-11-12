# IngeTrazo Extensions - AI Development Rules

## Development Environment
- Target application: IngeTrazo 0.6.0
- Operating system: Windows x64
- Default units: cm
- Interface language: Traditional Chinese
- Do not assume SketchUp Ruby API compatibility.
- Inspect the IngeTrazo source code and available APIs before implementation.

## Development Rules
- Do not modify code unless the user explicitly requests implementation.
- Preserve existing working functionality.
- Do not change model geometry, materials, groups, layers, or transforms unless requested.
- Keep each extension in its own directory.
- Maintain version history and CHANGELOG.md for every extension.
- Keep trial and release builds separate when applicable.
- Never claim a bug is fixed without reporting the actual tests performed.
- Avoid unrelated refactoring during bug fixes.

## Testing and Release
- Test nested groups, overlapping geometry, face generation, materials, undo/redo, and error handling where applicable.
- Distinguish automated tests from real IngeTrazo application tests.
- Do not mark a release as verified without application-level testing.
- Document known limitations and unresolved problems.
- Require user approval before publishing a formal release.

## Git Workflow
- Use a dedicated development branch for code changes.
- Describe changed files, reasons, tests, and risks.
- Do not overwrite previous official releases.
- Do not commit passwords, access tokens, or private credentials.
