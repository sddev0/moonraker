# Generic sensor support
#
# Copyright (C) 2022 Morton Jonuschat <mjonuschat+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to read additional generic sensor data and make it
# available to clients
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from functools import partial
from .sensor import Sensor, Sensors

# Annotation imports
from typing import (
    Any,
    DefaultDict,
    Deque,
    Dict,
    List,
    Tuple,
    Optional,
    Type,
    TYPE_CHECKING,
    Union,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest

class EnergyManager:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()
        self.meters: Dict[str, EnergyMeter] = {}
        self.job_state: str = None

        for section in config.get_prefix_sections("energymeter"):
            cfg = config[section]
            try:
                meter = EnergyMeter(cfg, self)
            except Exception as e:
                msg = f"Failed to load meter [{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg)
                continue
            self.meters[meter.get_name()] = meter

        self.server.register_endpoint(
            "/server/energy", ['GET'], self._handle_energy_request)
        logging.info("EnergyManager initialized")

        self.server.register_event_handler(
            "job_state:started", self._on_job_started)
        self.server.register_event_handler(
            "job_state:complete", self._on_job_complete)
        self.server.register_event_handler(
            "job_state:cancelled", self._on_job_cancelled)
        self.server.register_event_handler(
            "job_state:standby", self._on_job_standby)
        self.server.register_event_handler(
            "job_state:error", self._on_job_error)

    async def _handle_energy_request(
            self, web_request: WebRequest
            )-> Dict[str, Any]:
        result: Dict[str, Any] = {}

        total: Dict[str, Any] = {
            "power": round(self.get_total_power(), 2),
            "consumption": {
                "total": round(self.get_total_consumption(), 2)
            }
        }
        if self.job_state is not None and self.job_state != "idle":
            total["consumption"]["current_job"] = round(self.get_total_consumption_current_job(), 2)
        result["total"] = total
        
        for meter in self.meters.values():
            output: Dict [str, any] = {
                "power": round(meter.get_power(), 2),
                    "consumption": {
                        "total": round(meter.get_consumption(), 2)
                }
            }
            if self.job_state is not None and self.job_state != "idle":
                output["consumption"]["current_job"] = round(meter.get_consumption_current_job(), 2)
            
            result[meter.get_name()] = output

        return result
    
    def get_total_power(self) -> float:
        return sum(meter.get_power() for meter in self.meters.values() if meter.get_power() is not None)

    def get_total_consumption(self) -> float:
        return sum(meter.get_consumption() for meter in self.meters.values() if meter.get_consumption() is not None)

    def get_total_consumption_current_job(self) -> float:
        return sum(meter.get_consumption_current_job() for meter in self.meters.values() if meter.get_consumption_current_job() is not None)
    
    def _on_request_job_consumption(self) -> float:
        return 0.
    
    def _on_job_started(self,
                        prev_stats: Dict[str, Any],
                        new_stats: Dict[str, Any]
                        ) -> None:
        self.job_state = "printing"


    def _on_job_complete(self,
                         prev_stats: Dict[str, Any],
                         new_stats: Dict[str, Any]
                         ) -> None:
        self.job_state = "complete"

    def _on_job_cancelled(self,
                          prev_stats: Dict[str, Any],
                          new_stats: Dict[str, Any]
                          ) -> None:
        self.job_state = "complete"

    def _on_job_error(self,
                      prev_stats: Dict[str, Any],
                      new_stats: Dict[str, Any]
                      ) -> None:
        self.job_state = "complete"

    def _on_job_standby(self,
                        prev_stats: Dict[str, Any],
                        new_stats: Dict[str, Any]
                        ) -> None:
        self.job_state = "idle"

class EnergyMeter:
    def __init__(self, config: ConfigHelper, manager: EnergyManager):
        self.name = config.get_name().split(" ", maxsplit=1)[1]
        self.power_sensor = SensorLink(config.get("power_sensor"))
        self.consumption_sensor = SensorLink(config.get("consumption_sensor"))
        self.manager = manager

        self.power: float = 0.

        self.consumption: float = 0.
        self.consumption_last: float = None
        self.consumption_current_job: float = 0.

        config.server.register_event_handler(
            "sensors:sensor_update", self._handle_sensor_update)

    def _handle_sensor_update(self, sensor_data: Dict[str, Dict[str, Union[int, float]]]) -> None:
        if self.power_sensor.sensor in sensor_data:
            self._update_power(sensor_data[self.power_sensor.sensor])
        if self.consumption_sensor.sensor in sensor_data:
            self._update_consumption(sensor_data[self.consumption_sensor.sensor])

    def _update_power(self, new_sensor_data: Dict[str, Union[int, float]]) -> None:
        if self.power_sensor.field not in new_sensor_data:
            return
        
        self.power = new_sensor_data[self.power_sensor.field]

    def _update_consumption(self, new_sensor_data: Dict[str, Union[int, float]]) -> None:
        if self.consumption_sensor.field not in new_sensor_data:
            return
        
        current_consumption = new_sensor_data[self.consumption_sensor.field]
        increase: float
        if self.consumption_last is not None:
            if current_consumption >= self.consumption_last:
                # meter continued
                increase = current_consumption - self.consumption_last
            else:
                # meter was reset or had rollover
                increase = current_consumption
        else:
            increase = 0.
        
        self.consumption += increase

        if self.manager.job_state == "printing":
            self.consumption_current_job += increase

        self.consumption_last = current_consumption

    def get_power(self) -> float:
        return self.power
    
    def get_consumption(self) -> float:
        return self.consumption
    
    def get_consumption_current_job(self) -> float:
        return self.consumption_current_job

    def get_name(self) -> str:
        return self.name
    
class SensorLink:
    sensor: str
    field: str
    def __init__(self, sensor_path: str):
        self.sensor, self.field = sensor_path.split(".", maxsplit=1)

def load_component(config: ConfigHelper) -> EnergyManager:
    return EnergyManager(config)
