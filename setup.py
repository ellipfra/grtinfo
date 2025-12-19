#!/usr/bin/env python3
"""Setup script to install grtinfo CLI tools"""

from setuptools import setup

setup(
    name='grtinfo',
    version='1.0.0',
    description='CLI tools to analyze TheGraph Network indexers, delegators, allocations and curation signals',
    py_modules=['subinfo', 'indexerinfo', 'delegatorinfo'],
    install_requires=[
        'requests>=2.31.0',
        'web3>=6.0.0',
    ],
    entry_points={
        'console_scripts': [
            'subinfo=subinfo:main',
            'indexerinfo=indexerinfo:main',
            'delegatorinfo=delegatorinfo:main',
        ],
    },
    python_requires='>=3.7',
    license='MIT',
    author='',
    url='https://github.com/ellipfra/grtinfo',
)
