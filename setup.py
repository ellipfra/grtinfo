#!/usr/bin/env python3
"""Setup script to install subinfo as a CLI command"""

from setuptools import setup

setup(
    name='subinfo',
    version='1.0.0',
    description='CLI tool to analyze TheGraph allocations and curation signals',
    py_modules=['subinfo'],
    install_requires=[
        'requests>=2.31.0',
    ],
    entry_points={
        'console_scripts': [
            'subinfo=subinfo:main',
        ],
    },
    python_requires='>=3.7',
    license='MIT',
    author='',
    url='https://github.com/yourusername/subinfo',
)
