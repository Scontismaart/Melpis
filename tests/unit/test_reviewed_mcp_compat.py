"""Offline MCP compatibility; no application secrets or real LLM/provider calls."""
import importlib.metadata
import inspect
import asyncio
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_crewai(monkeypatch, tmp_path):
    monkeypatch.setenv('CREWAI_TELEMETRY_ENABLED', 'false')
    monkeypatch.setenv('CREWAI_TRACING_ENABLED', 'false')
    monkeypatch.setenv('OTEL_SDK_DISABLED', 'true')
    monkeypatch.setenv('CREWAI_STORAGE_DIR', str(tmp_path / 'crewai'))


def test_real_mcp_sdk_version_and_call_signatures():
    from mcp import StdioServerParameters
    from mcp.client.streamable_http import streamablehttp_client
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client

    assert importlib.metadata.version('mcp') == '1.30.0'
    assert importlib.metadata.version('crewai') == '1.15.4'
    inspect.signature(streamablehttp_client).bind('http://synthetic.invalid/mcp', headers=None, terminate_on_close=True)
    inspect.signature(sse_client).bind('http://synthetic.invalid/sse', headers=None)
    inspect.signature(stdio_client).bind(StdioServerParameters(command=sys.executable, args=[]))


async def test_real_stdio_client_discovers_calls_and_reports_tool_errors(tmp_path):
    from crewai.mcp.client import MCPClient
    from crewai.mcp.transports.stdio import StdioTransport

    server = tmp_path / 'synthetic_server.py'
    server.write_text('''from mcp.server.fastmcp import FastMCP
server=FastMCP("Synthetic offline compatibility")
@server.tool()
def echo(value:str)->str:
    return "synthetic:"+value
@server.tool()
def failure()->str:
    raise ValueError("synthetic failure")
server.run(transport="stdio")
''', encoding='utf-8')
    transport = StdioTransport(sys.executable, args=[str(server)])
    async with MCPClient(transport, max_retries=1) as client:
        assert any(tool.get('name') == 'echo' for tool in await client.list_tools())
        assert 'synthetic:offline' in str(await client.call_tool('echo', {'value': 'offline'}))
        assert 'synthetic failure' in str(await client.call_tool('failure', {}))


async def test_http_and_sse_transport_context_contract_without_network():
    from crewai.mcp.transports.http import HTTPTransport
    from crewai.mcp.transports.sse import SSETransport

    @asynccontextmanager
    async def http_context(url, headers=None, terminate_on_close=True):
        assert url == 'http://synthetic.invalid/mcp'
        assert terminate_on_close is True
        yield ('read', 'write', lambda: None)

    @asynccontextmanager
    async def sse_context(url, headers=None):
        assert url == 'http://synthetic.invalid/sse'
        yield ('read', 'write')

    with patch('mcp.client.streamable_http.streamablehttp_client', http_context):
        transport = HTTPTransport('http://synthetic.invalid/mcp')
        await transport.connect()
        assert transport.connected
        await transport.disconnect()
        assert not transport.connected
    with patch('mcp.client.sse.sse_client', sse_context):
        transport = SSETransport('http://synthetic.invalid/sse')
        await transport.connect()
        assert transport.connected
        await transport.disconnect()
        assert not transport.connected


def test_crewai_kickoff_uses_only_the_synthetic_llm():
    from crewai import Agent, Crew, Process, Task
    from crewai.llms.base_llm import BaseLLM

    class SyntheticLLM(BaseLLM):
        def __init__(self):
            super().__init__(model='synthetic-offline', temperature=0)

        def call(self, *args, **kwargs):
            return 'Final Answer: synthetic-success'

        def supports_function_calling(self):
            return False

        def get_context_window_size(self):
            return 4096

    agent = Agent(role='Synthetic verifier', goal='Return synthetic result',
                  backstory='No external provider', llm=SyntheticLLM(),
                  verbose=False, allow_delegation=False, max_iter=2)
    task = Task(description='Return synthetic result', expected_output='synthetic-success', agent=agent)
    result = Crew(agents=[agent], tasks=[task], process=Process.sequential,
                  verbose=False, memory=False).kickoff()
    assert 'synthetic-success' in str(result)


@pytest.mark.parametrize('protocol', ['streamable-http', 'sse'])
async def test_real_loopback_http_and_sse_handshake_tools_and_errors(tmp_path, protocol):
    """Actual MCP wire protocols; bind loopback only and never call a provider."""
    from crewai.mcp.client import MCPClient
    from crewai.mcp.transports.http import HTTPTransport
    from crewai.mcp.transports.sse import SSETransport

    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    server = tmp_path / 'loopback_server.py'
    server.write_text('''import sys
from mcp.server.fastmcp import FastMCP
server=FastMCP("Synthetic loopback compatibility",host="127.0.0.1",port=int(sys.argv[2]),log_level="ERROR")
@server.tool()
def echo(value:str)->str:
    return "synthetic:"+value
@server.tool()
def failure()->str:
    raise ValueError("synthetic failure")
server.run(transport=sys.argv[1])
''', encoding='utf-8')
    # Fresh cwd has no .env. Do not forward credentials or provider environment.
    allowed = ('PATH', 'SystemRoot', 'WINDIR', 'LANG', 'LC_ALL')
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path),
                       CREWAI_TELEMETRY_ENABLED='false', OTEL_SDK_DISABLED='true')
    process = subprocess.Popen([sys.executable, str(server), protocol, str(port)],
                               cwd=tmp_path, env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            assert process.poll() is None, 'Synthetic loopback server exited before readiness'
            try:
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                if asyncio.get_running_loop().time() > deadline:
                    pytest.fail('Synthetic loopback server readiness timed out')
                await asyncio.sleep(0.05)

        async def exercise_protocol():
            transport = (HTTPTransport(f'http://127.0.0.1:{port}/mcp')
                         if protocol == 'streamable-http'
                         else SSETransport(f'http://127.0.0.1:{port}/sse'))
            async with MCPClient(transport, connect_timeout=5, execution_timeout=5,
                                 discovery_timeout=5, max_retries=1) as client:
                assert any(tool.get('name') == 'echo' for tool in await client.list_tools())
                assert 'synthetic:wire' in str(await client.call_tool('echo', {'value': 'wire'}))
                assert 'synthetic failure' in str(await client.call_tool('failure', {}))

        await asyncio.wait_for(exercise_protocol(), timeout=20)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
