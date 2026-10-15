"""Stable artifact types independent of OS registry and application associations."""
import mimetypes


# MimeTypes starts with Python's built-in tables; unlike the global guess_type,
# this private instance does not merge Windows registry or host mime.types files.
_TYPES = mimetypes.MimeTypes(filenames=())
for extension, media in {'.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
                         '.json': 'application/json', '.png': 'image/png',
                         '.md': 'text/markdown', '.py': 'text/x-python'}.items():
    _TYPES.add_type(media, extension)


def artifact_media_type(name):
    return _TYPES.guess_type(name)[0] or 'application/octet-stream'
