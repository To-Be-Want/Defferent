# Copyright 2025 ZTE Corporation.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Compatibility entry point for existing server service configurations.

Both this historical path and cloud_app.py serve the same scoped learning app.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cloud_app import app, create_app

if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="星轨智学服务")
    parser.add_argument("-p", "--port", type=int, default=7788)
    args = parser.parse_args()
    uvicorn.run(app, host=os.getenv("COSIGHT_HOST", "127.0.0.1"),
                port=args.port, ws_max_size=128 * 1024)
