"""Validate NAS storage before accepting work."""
import os
from migrate_catalog import migrate

if __name__ == '__main__':
    migrate('/seed/catalog', '/data/catalog', '/media', os.environ['SOURCE_ROOT'])
    for key in ('OPENLUX_API_KEY', 'RERANK_API_KEY', 'SUNO_API_KEY', 'MIXER_API_TOKEN'):
        if not os.environ.get(key):
            raise ValueError(f'缺少 {key}')
    import uvicorn
    uvicorn.run('nas_api:create_app', factory=True, host='0.0.0.0', port=8780,
                workers=1, access_log=False)
