PROFILE = {
    "name": "ina",
    "title": "INA Sensor",
    "sensor_types": ["solar", "wind", "battery", "system"],
    "variant_options": ["INA219", "INA226", "INA228", "INA237", "INA238", "INA260", "INA3221"],
    "external_shunt_options": [
        {"value": "75mv_50a", "label": "50A / 75mV", "max_current_amps": 50.0, "shunt_mv": 75.0},
        {"value": "75mv_100a", "label": "100A / 75mV", "max_current_amps": 100.0, "shunt_mv": 75.0},
        {"value": "75mv_150a", "label": "150A / 75mV", "max_current_amps": 150.0, "shunt_mv": 75.0},
        {"value": "75mv_200a", "label": "200A / 75mV", "max_current_amps": 200.0, "shunt_mv": 75.0},
        {"value": "75mv_300a", "label": "300A / 75mV", "max_current_amps": 300.0, "shunt_mv": 75.0},
    ],
}