from setuptools import setup
from pathlib import Path
import re


def read_src_version():
    p = (Path(__file__).parent / 'ai' / 'backend' / 'agent' / '__init__.py')
    src = p.read_text()
    m = re.search(r"^__version__\s*=\s*'([^']+)'", src, re.M)
    return m.group(1)


requires = [
    'ConfigArgParse==0.12',
    'coloredlogs>=5.2',
    'dataclasses; python_version<"3.7"',
    'pyzmq>=17.0',
    'aiodocker',
    'aiozmq>=0.7',
    'aiohttp~=3.3.0',
    'aioredis~=1.0.0',
    'aiobotocore>=0.3.0',
    'aiotools>=0.6.0',
    'async_timeout~=3.0',
    'attrs>=18.0',  # to avoid pip 10 resolver issue
    'psutil~=5.4.0',
    'python-snappy~=0.5.1',
    'pyzmq>=17.0',
    'requests',
    'requests_unixsocket',
    'trafaret>=1.0',
    'uvloop~=0.11.0',
    'backend.ai-common',
]
build_requires = [
    'wheel>=0.31.0',
    'twine>=1.11.0',
]
test_requires = [
    'pytest>=3.7.0',
    'pytest-asyncio>=0.3.0',
    'pytest-cov',
    'pytest-mock',
    'asynctest',
    'flake8',
    'codecov',
]
dev_requires = build_requires + test_requires + [
    'pytest-sugar',
]
ci_requires = []
monitor_requires = [
    'datadog>=0.16.0',
    'raven>=6.1',
]


setup(
    name='backend.ai-agent',
    version=read_src_version(),
    description='Backend.AI Agent',
    long_description=Path('README.md').read_text(),
    long_description_content_type='text/markdown',
    url='https://github.com/lablup/backend.ai-agent',
    author='Lablup Inc.',
    author_email='joongi@lablup.com',
    license='LGPLv3',
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)',  # noqa
        'Intended Audience :: Developers',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Operating System :: POSIX',
        'Operating System :: MacOS :: MacOS X',
        'Environment :: No Input/Output (Daemon)',
        'Topic :: Scientific/Engineering',
        'Topic :: Software Development',
    ],

    packages=['ai.backend.agent'],

    python_requires='>=3.6,<3.7',
    setup_requires=['setuptools>=40.1.0'],
    install_requires=requires,
    extras_require={
        'build': build_requires,
        'test': test_requires,
        'dev': dev_requires,
        'ci': ci_requires,
        'monitor': monitor_requires,
    },
)
