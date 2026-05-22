import os
import time

import torch
import inspect
import execution
import server
from aiohttp import web

try:
    import psutil
except Exception:  # noqa
    psutil = None


# import model_management


class ExecutionTime:
    CATEGORY = "TyDev-Utils/Debug"

    @classmethod
    def INPUT_TYPES(s):
        return {"required": {}}

    RETURN_TYPES = ()
    RETURN_NAMES = ()
    FUNCTION = "process"

    def process(self):
        return ()


CURRENT_START_EXECUTION_DATA = None
EXECUTION_TIME_LOGGING_ENABLED = False


# def get_free_vram():
#     dev = model_management.get_torch_device()
#     if hasattr(dev, 'type') and (dev.type == 'cpu' or dev.type == 'mps'):
#         return 0
#     else:
#         return model_management.get_free_memory(dev)


def get_peak_memory():
    if not torch.cuda.is_available():
        return 0
    device = torch.device('cuda')
    return torch.cuda.max_memory_allocated(device)


def reset_peak_memory_record():
    if not torch.cuda.is_available():
        return
    device = torch.device('cuda')
    torch.cuda.reset_max_memory_allocated(device)


def get_current_ram_usage():
    if psutil is not None:
        try:
            return psutil.Process(os.getpid()).memory_info().rss
        except Exception:  # noqa
            pass

    try:
        with open('/proc/self/statm', 'r', encoding='utf-8') as statm:
            rss_pages = int(statm.readline().split()[1])
        return rss_pages * os.sysconf('SC_PAGE_SIZE')
    except Exception:  # noqa
        return 0


def handle_execute(class_type, last_node_id, prompt_id, server, unique_id):
    if not CURRENT_START_EXECUTION_DATA:
        return
    start_time = CURRENT_START_EXECUTION_DATA['nodes_start_perf_time'].get(unique_id)
    start_vram = CURRENT_START_EXECUTION_DATA['nodes_start_vram'].get(unique_id)
    start_ram = CURRENT_START_EXECUTION_DATA['nodes_start_ram'].get(unique_id)
    if start_time:
        end_time = time.perf_counter()
        execution_time = end_time - start_time

        end_vram = get_peak_memory()
        vram_used = end_vram - start_vram if start_vram is not None else 0

        end_ram = get_current_ram_usage()
        ram_used = end_ram - start_ram if start_ram is not None else 0

        if server.client_id is not None and last_node_id != server.last_node_id:
            server.send_sync(
                "TyDev-Utils.ExecutionTime.executed",
                {"node": unique_id, "prompt_id": prompt_id, "execution_time": int(execution_time * 1000),
                 "vram_used": vram_used, "ram_used": ram_used},
                server.client_id
            )
        if EXECUTION_TIME_LOGGING_ENABLED:
            print(f"#{unique_id} [{class_type}]: {execution_time:.2f}s - vram {vram_used}b - ram {ram_used}b")


try:
    origin_execute = execution.execute

    if inspect.iscoroutinefunction(origin_execute):
        async def dev_utils_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id,
                                    execution_list, pending_subgraph_results, pending_async_nodes, *args, **kwargs):
            unique_id = current_item
            class_type = dynprompt.get_node(unique_id)['class_type']
            last_node_id = server.last_node_id
            result = await origin_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id,
                                          execution_list, pending_subgraph_results, pending_async_nodes, *args, **kwargs)
            handle_execute(class_type, last_node_id, prompt_id, server, unique_id)
            return result
    else:
        def dev_utils_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id,
                              execution_list, pending_subgraph_results, *args, **kwargs):
            unique_id = current_item
            class_type = dynprompt.get_node(unique_id)['class_type']
            last_node_id = server.last_node_id
            result = origin_execute(server, dynprompt, caches, current_item, extra_data, executed, prompt_id,
                                    execution_list, pending_subgraph_results, *args, **kwargs)
            handle_execute(class_type, last_node_id, prompt_id, server, unique_id)
            return result

    execution.execute = dev_utils_execute
except Exception as e:
    pass

# region: Deprecated
try:
    # The execute method in the old version of ComfyUI is now deprecated.
    origin_recursive_execute = execution.recursive_execute


    def dev_utils_origin_recursive_execute(server, prompt, outputs, current_item, extra_data, executed, prompt_id,
                                           outputs_ui,
                                           object_storage):
        unique_id = current_item
        class_type = prompt[unique_id]['class_type']
        last_node_id = server.last_node_id
        result = origin_recursive_execute(server, prompt, outputs, current_item, extra_data, executed, prompt_id,
                                          outputs_ui,
                                          object_storage)
        handle_execute(class_type, last_node_id, prompt_id, server, unique_id)
        return result


    execution.recursive_execute = dev_utils_origin_recursive_execute
except Exception as e:
    pass
# endregion

origin_func = server.PromptServer.send_sync


def dev_utils_send_sync(self, event, data, sid=None):
    # print(f"dev_utils_send_sync, event: {event}, data: {data}")
    global CURRENT_START_EXECUTION_DATA
    if event == "execution_start":
        CURRENT_START_EXECUTION_DATA = dict(
            start_perf_time=time.perf_counter(),
            start_ram=get_current_ram_usage(),
            nodes_start_perf_time={},
            nodes_start_vram={},
            nodes_start_ram={}
        )

    origin_func(self, event=event, data=data, sid=sid)

    if event == "executing" and data and CURRENT_START_EXECUTION_DATA:
        if data.get("node") is None:
            if sid is not None:
                start_perf_time = CURRENT_START_EXECUTION_DATA.get('start_perf_time')
                start_ram = CURRENT_START_EXECUTION_DATA.get('start_ram')
                new_data = data.copy()
                if start_perf_time is not None:
                    execution_time = time.perf_counter() - start_perf_time
                    new_data['execution_time'] = int(execution_time * 1000)
                if start_ram is not None:
                    new_data['ram_used'] = get_current_ram_usage() - start_ram
                origin_func(
                    self,
                    event="TyDev-Utils.ExecutionTime.execution_end",
                    data=new_data,
                    sid=sid
                )
        else:
            node_id = data.get("node")
            CURRENT_START_EXECUTION_DATA['nodes_start_perf_time'][node_id] = time.perf_counter()
            reset_peak_memory_record()
            CURRENT_START_EXECUTION_DATA['nodes_start_vram'][node_id] = get_peak_memory()
            CURRENT_START_EXECUTION_DATA['nodes_start_ram'][node_id] = get_current_ram_usage()


server.PromptServer.send_sync = dev_utils_send_sync


@server.PromptServer.instance.routes.post("/ty-dev-utils/execution-time/logging")  # noqa
async def set_execution_time_logging(request: web.Request) -> web.StreamResponse:
    global EXECUTION_TIME_LOGGING_ENABLED
    data = await request.json()
    EXECUTION_TIME_LOGGING_ENABLED = bool(data.get('enabled'))
    return web.json_response({'enabled': EXECUTION_TIME_LOGGING_ENABLED})
