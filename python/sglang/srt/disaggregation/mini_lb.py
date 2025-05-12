"""
Minimal HTTP load balancer for prefill and decode servers for testing.
"""

import asyncio
import random
import urllib
from itertools import chain
from typing import List
import os
import time

import aiohttp
import orjson
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import ORJSONResponse, Response, StreamingResponse


class PrefillConfig:
    def __init__(self, url: str, bootstrap_port: int):
        self.url = url
        self.bootstrap_port = bootstrap_port


class MiniLoadBalancer:
    def __init__(self, prefill_configs: List[PrefillConfig], decode_servers: List[str]):
        self.prefill_configs = prefill_configs
        self.prefill_servers = [p.url for p in prefill_configs]
        self.decode_servers = decode_servers
        self.profiling = False
        self.recording = False
        self.round_robin_counter = 0

        profile_dir = os.getenv("SGLANG_TORCH_PROFILER_DIR", "./tmp")
        os.makedirs(profile_dir, exist_ok=True)

    def select_pair(self):
        # prefill_config = random.choice(self.prefill_configs)

        # using round_robin in selecting prefill nodes
        prefill_config = self.prefill_configs[self.round_robin_counter]
        self.round_robin_counter = (self.round_robin_counter + 1) % len(
            self.prefill_configs
        )

        decode_server = random.choice(self.decode_servers)
        return prefill_config.url, prefill_config.bootstrap_port, decode_server

    async def start_profile(self):
        """Start profiling on all servers."""
        if self.profiling:
            return {"success": False, "message": "Profiling is already in progress"}

        self.profiling = True
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/start_profile"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "Profiling started" if success else "Failed to start profiling"}

    async def stop_profile(self):
        """Stop profiling on all servers."""
        if not self.profiling:
            return {"success": False, "message": "Profiling is not in progress"}

        self.profiling = False
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/stop_profile"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "Profiling stopped" if success else "Failed to stop profiling"}

    async def start_expert_distribution_record(self):
        if self.recording:
            return {"success": False, "message": "Recoding is already in progress"}

        self.recording = True
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/start_expert_distribution_record"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "Recording expert distribution started" if success else "Failed to start recording expert distribution"}

    async def stop_expert_distribution_record(self):
        if not self.recording:
            return {"success": False, "message": "Recoding is not in progress"}

        self.recording = False
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/stop_expert_distribution_record"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "Recording expert distribution stopped" if success else "Failed to stop recording expert distribution"}

    async def dump_expert_distribution_record(self):
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/dump_expert_distribution_record"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "Dumping expert distribution succeed" if success else "Failed to dumping expert distribution"}

    async def eplb_rebalance(self):
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/eplb_rebalance"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "EPLB rebalanced" if success else "Failed to rebalancing EPLB."}

    async def eplb_save_expert_distribution(self):
        async with aiohttp.ClientSession() as session:
            tasks = []
            for server in chain(self.prefill_servers, self.decode_servers):
                tasks.append(session.post(f"{server}/eplb_save_expert_distribution"))

            responses = await asyncio.gather(*tasks)
            success = all(response.status == 200 for response in responses)
            return {"success": success, "message": "Saving expert distribution succeed" if success else "Failed to saving expert distribution."}

    async def generate(
        self, modified_request, prefill_server, decode_server, endpoint
    ) -> ORJSONResponse:
        assert endpoint[0] != "/", f"Endpoint should not start with '/': {endpoint}"

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(
                total=3600
            )  # Add timeout for request reliability
        ) as session:
            tasks = [
                session.post(f"{prefill_server}/{endpoint}", json=modified_request),
                session.post(f"{decode_server}/{endpoint}", json=modified_request),
            ]
            # Wait for both responses to complete. Prefill should end first.
            prefill_response, decode_response = await asyncio.gather(*tasks)

            return ORJSONResponse(
                content=await decode_response.json(),
                status_code=decode_response.status,
            )

    async def generate_stream(
        self, modified_request, prefill_server, decode_server, endpoint="generate"
    ):
        assert endpoint[0] != "/", f"Endpoint should not start with '/': {endpoint}"

        async def stream_results():
            prefill_response = None
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(
                    total=3600
                )  # Add timeout for request reliability
            ) as session:
                try:
                    # Create the tasks for both prefill and decode requests
                    tasks = [
                        session.post(
                            f"{prefill_server}/{endpoint}", json=modified_request
                        ),
                        session.post(
                            f"{decode_server}/{endpoint}", json=modified_request
                        ),
                    ]
                    # Wait for both responses to complete. Since this is streaming, they return immediately.
                    prefill_response, decode_response = await asyncio.gather(*tasks)
                    async for chunk in decode_response.content:
                        yield chunk
                except Exception as e:
                    error_msg = {
                        "error": {"message": f"Stream processing error: {str(e)}"}
                    }
                    yield b"data: " + orjson.dumps(
                        error_msg, option=orjson.OPT_NON_STR_KEYS
                    ) + b"\n\n"
                finally:
                    if prefill_response is not None:
                        await prefill_response.release()

        return StreamingResponse(
            stream_results(),
            media_type="text/event-stream",
        )


app = FastAPI()
load_balancer = None


@app.get("/health")
async def health_check():
    return Response(status_code=200)


@app.get("/health_generate")
async def health_check():
    prefill_servers, decode_servers = (
        load_balancer.prefill_servers,
        load_balancer.decode_servers,
    )
    async with aiohttp.ClientSession() as session:
        # Create the tasks
        tasks = []
        for server in chain(prefill_servers, decode_servers):
            tasks.append(session.post(f"{server}/health_generate"))
        for i, response in enumerate(asyncio.as_completed(tasks)):
            await response
    return Response(status_code=200)


@app.post("/flush_cache")
async def flush_cache():
    prefill_servers, decode_servers = (
        load_balancer.prefill_servers,
        load_balancer.decode_servers,
    )
    async with aiohttp.ClientSession() as session:
        # Create the tasks
        tasks = []
        for server in chain(prefill_servers, decode_servers):
            tasks.append(session.post(f"{server}/flush_cache"))
        for i, response in enumerate(asyncio.as_completed(tasks)):
            await response
    return Response(status_code=200)


@app.get("/get_server_info")
async def get_server_info():
    prefill_servers, decode_servers = (
        load_balancer.prefill_servers,
        load_balancer.decode_servers,
    )
    prefill_infos = []
    decode_infos = []
    async with aiohttp.ClientSession() as session:
        for server in chain(prefill_servers):
            server_info = await session.get(f"{server}/get_server_info")
            prefill_infos.append(await server_info.json())
        for server in chain(decode_servers):
            server_info = await session.get(f"{server}/get_server_info")
            decode_infos.append(await server_info.json())

    return {"prefill": prefill_infos, "decode": decode_infos}


@app.get("/get_model_info")
async def get_model_info():
    # Dummy model information
    model_info = {
        "model_path": "/path/to/dummy/model",
        "tokenizer_path": "/path/to/dummy/tokenizer",
        "is_generation": True,
        "preferred_sampling_params": {"temperature": 0.7, "max_new_tokens": 128},
    }
    return ORJSONResponse(content=model_info)


@app.post("/generate")
async def handle_generate_request(request_data: dict):
    prefill_server, bootstrap_port, decode_server = load_balancer.select_pair()

    # Parse and transform prefill_server for bootstrap data
    parsed_url = urllib.parse.urlparse(prefill_server)
    hostname = parsed_url.hostname
    modified_request = request_data.copy()

    batch_size = _get_request_batch_size(modified_request)
    if batch_size is not None:
        modified_request.update(
            {
                "bootstrap_host": [hostname] * batch_size,
                "bootstrap_port": [bootstrap_port] * batch_size,
                "bootstrap_room": [
                    _generate_bootstrap_room() for _ in range(batch_size)
                ],
            }
        )
    else:
        modified_request.update(
            {
                "bootstrap_host": hostname,
                "bootstrap_port": bootstrap_port,
                "bootstrap_room": _generate_bootstrap_room(),
            }
        )

    if request_data.get("stream", False):
        return await load_balancer.generate_stream(
            modified_request, prefill_server, decode_server, "generate"
        )
    else:
        return await load_balancer.generate(
            modified_request, prefill_server, decode_server, "generate"
        )


@app.post("/v1/chat/completions")
async def handle_completion_request(request_data: dict):
    prefill_server, bootstrap_port, decode_server = load_balancer.select_pair()

    # Parse and transform prefill_server for bootstrap data
    parsed_url = urllib.parse.urlparse(prefill_server)
    hostname = parsed_url.hostname
    modified_request = request_data.copy()
    req_id = modified_request.get("req_id", None)
    bootstrap_room = req_id if req_id is not None else random.randint(0, 2**63 - 1)
    print(f"bootstrap_room: {bootstrap_room}")
    modified_request.update(
        {
            "bootstrap_host": hostname,
            "bootstrap_port": bootstrap_port,
            "bootstrap_room": bootstrap_room,
        }
    )

    if request_data.get("stream", False):
        return await load_balancer.generate_stream(
            modified_request,
            prefill_server,
            decode_server,
            endpoint="v1/chat/completions",
        )
    else:
        return await load_balancer.generate(
            modified_request,
            prefill_server,
            decode_server,
            endpoint="v1/chat/completions",
        )


def _generate_bootstrap_room():
    return random.randint(0, 2**63 - 1)


# We may utilize `GenerateReqInput`'s logic later
def _get_request_batch_size(request):
    if (text := request.get("text")) is not None:
        return None if isinstance(text, str) else len(text)
    if (input_ids := request.get("input_ids")) is not None:
        return None if isinstance(input_ids[0], int) else len(input_ids)
    return None


@app.get("/v1/models")
async def get_models():
    prefill_server = load_balancer.prefill_servers[0]  # Get the first prefill server
    async with aiohttp.ClientSession() as session:
        try:
            response = await session.get(f"{prefill_server}/v1/models")
            if response.status != 200:
                raise HTTPException(
                    status_code=response.status,
                    detail=f"Prefill server error: Status {response.status}",
                )
            return ORJSONResponse(content=await response.json())
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/start_profile")
async def start_profile():
    """Start profiling on all servers."""
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.start_profile()

@app.post("/stop_profile")
async def stop_profile():
    """Stop profiling on all servers."""
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.stop_profile()

@app.post("/start_expert_distribution_record")
async def start_expert_distribution_record():
    """Start recording the expert distribution. Clear the previous record if any."""
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.start_expert_distribution_record()

@app.post("/stop_expert_distribution_record")
async def stop_expert_distribution_record():
    """Stop recording the expert distribution."""
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.stop_expert_distribution_record()

@app.post("/dump_expert_distribution_record")
async def dump_expert_distribution_record():
    """Dump expert distribution record."""
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.dump_expert_distribution_record()

@app.post("/eplb_rebalance")
async def eplb_rebalance():
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.eplb_rebalance()

@app.post("/eplb_save_expert_distribution")
async def eplb_save_expert_distribution():
    if load_balancer is None:
        raise HTTPException(status_code=500, detail="Load balancer not initialized")
    return await load_balancer.eplb_save_expert_distribution()


def run(prefill_configs, decode_addrs, host, port):
    global load_balancer
    load_balancer = MiniLoadBalancer(prefill_configs, decode_addrs)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mini Load Balancer Server")
    parser.add_argument(
        "--prefill", required=True, help="Comma-separated URLs for prefill servers"
    )
    parser.add_argument(
        "--prefill-bootstrap-ports",
        help="Comma-separated bootstrap ports for prefill servers",
        default="8998",
    )
    parser.add_argument(
        "--decode", required=True, help="Comma-separated URLs for decode servers"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind the server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to bind the server (default: 8000)"
    )
    args = parser.parse_args()

    prefill_urls = args.prefill.split(",")
    bootstrap_ports = [int(p) for p in args.prefill_bootstrap_ports.split(",")]

    if len(bootstrap_ports) == 1:
        bootstrap_ports = bootstrap_ports * len(prefill_urls)
    else:
        if len(bootstrap_ports) != len(prefill_urls):
            raise ValueError(
                "Number of prefill URLs must match number of bootstrap ports"
            )
            exit(1)

    prefill_configs = []
    for url, port in zip(prefill_urls, bootstrap_ports):
        prefill_configs.append(PrefillConfig(url, port))

    decode_addrs = args.decode.split(",")

    run(prefill_configs, decode_addrs, args.host, args.port)
