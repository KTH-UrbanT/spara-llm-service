import re
import requests
import os
import logging
import time

class Spec:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        logger = logging.getLogger()
        logging.basicConfig(level=logging.INFO)

    def __repr__(self):
        return f"<BuildingData byggnadsid={getattr(self, 'byggnadsid', 'N/A')}>"

    def __str__(self):
        attrs = [
            f"{key}={value!r}" 
            for key, value in self.__dict__.items() 
            if value is not None
        ]
        return f"<Spec {' '.join(attrs)}>"

    #def __str__(self):
    #    return f" Address: {self.address}, Elkonsumtion: {self.el_calc}, Värmekonsumtion: {self.varm_calc}, Källarplan: {self.basement}, Antal Trapphus: {self.stairs}, Energiklass: {self.energy_class}, Primär värme: {self.primary_heat}"
        
class Building_specs:
    def __init__(self):
        self.building = None
        self.address = "ingen address info"
        self.logger = logging.getLogger()
        logging.basicConfig(level=logging.INFO)
    
    def update_address(self, prompt):
        pattern = re.compile(r"\b(?:bor på|address är|live at|live in|address is|live on)\b\W+(\w+(?:\W+\w+){0,1})", re.IGNORECASE)
        address_matches = pattern.findall(prompt)
        print("updating address...")

        # 1. If regex does not match and there already is a spec, return self.building.__str__()
        if not address_matches:
            if self.building:
                return self.building.__str__()
            else:
                return self.address

        found_address = address_matches[0]

        # 2. If regex matches and self.address matches the found address, return self.building.__str__()
        if found_address == self.address:
            if self.building:
                return self.building.__str__()
            else:
                return self.address

        # 3. If regex matches, address doesn't match current, try to fetch new spec
        def get_address_variant(address, offset):
            match = re.match(r"(.+?)(\d+)([A-Za-z]*)$", address)
            if not match:
                return address
            street, number, letter = match.groups()
            try:
                new_number = int(number) + offset
                return f"{street}{new_number}{letter}"
            except ValueError:
                return address

        host = os.getenv("ODEN_API_host")
        port = os.getenv("ODEN_API_port")
        attempts = [found_address, get_address_variant(found_address, 2), get_address_variant(found_address, -2)]

        for addr in attempts:
            req = f"https://{host}:{port}/api/v1/buildings/single_filter?filter_name=epc_idadr&filter_value={addr}"
            try:
                result = requests.get(req, timeout=10)
                print(f"API request for address: {addr}, status: {result.status_code}")
                if result.status_code == 200:
                    data = result.json()
                    if isinstance(data, list) and data:
                        spec = Spec(**data[0])
                        self.address = addr
                        self.building = spec
                        self.logger.info(self.address)
                        self.logger.debug(spec.__str__())
                        # 3. Success: return new spec
                        return self.building.__str__()
                    else:
                        print("API returned empty or invalid data:", data)
                        self.logger.info("API returned empty or invalid data")
                elif result.status_code == 404:
                    print(f"Address {addr} not found, trying next variant...")
                    time.sleep(0.75)
                    continue
                else:
                    status = result.status_code
                    error = result.reason
                    print(f"API failed with: {status}, {error}")
                    self.logger.info(f"API failed with: {status}, {error}")
                    break
            except requests.exceptions.RequestException as e:
                print(f"API exception: {e}")
                self.logger.info(f"API exception: {e}")
                break

        # 4. If all attempts fail, set to "ingen address info" and return it
        self.building = None
        self.address = "ingen address info"
        print("No matching address found after variants.")
        return self.address

            

       

    building: Spec
    address: str 


'''
def search_for_address(prompt, buildings):
    address_matches = pattern.findall(prompt)

    if not address_matches:
        print("No address found.")
        return None

    base_address = address_matches[0]
    print(f"Trying address: {base_address}")

    def get_address_variant(address, offset):
        # Assumes address format: "StreetName Number[Letter]"
        if not match:
            return address  # fallback if format is unexpected
        street, number, letter = match.groups()
        try:
            new_number = int(number) + offset
            return f"{street}{new_number}{letter}"
        except ValueError:
            return address

    attempts = [base_address, get_address_variant(base_address, 2), get_address_variant(base_address, -2)]
    for addr in attempts:
        req = f"http://:8001/buildings/single_filter?filter_name=epc_idadr&filter_value={addr}"
        try:
            result = requests.get(req, timeout=7)
            print(f"API request for address: {addr}, status: {result.status_code}")
            if result.status_code == 200:
                print(result.text)
                print(addr)
                return result.text
            elif result.status_code == 404:
                print(f"Address {addr} not found, trying next variant...")
                time.sleep(0.5)  # brief pause between retries
            else:
                print(f"API error: {result.status_code} {result.reason}")
                break
        except requests.exceptions.RequestException as e:
            print(f"API exception: {e}")
            break

    print("No matching address found after variants.")
    return None
'''

def main(prompt):
    #df = pd.read_csv("buildings.csv", sep=";", usecols=["IdAdr", "El_calc", "EgiVarme_calc", "EgenAntalKallarplan", "EgenAntalPlan", "EgenAntalTrapphus", "EgiEnergiklass2020_calc", "HuvudsakligUppvarmning_calc"], skipinitialspace=True)
    #buildings = []
    #for _, row in df.iterrows():
    #    spec = Spec(
    #        address=row["IdAdr"],
    #        el_calc=row["El_calc"],
    #        varme_calc=row["EgiVarme_calc"],
    #        basebent=row["EgenAntalKallarplan"],
    #        stairs=row["EgenAntalTrapphus"],
    #        energy_class=row["EgiEnergiklass2020_calc"],
    #        primary_heat=row["HuvudsakligUppvarmning_calc"]
    #    )
    #    buildings.append(spec)
    #print(df)
    #spec = search_for_address(prompt, buildings)
    #print(spec)
    #return spec.__str__()
    if spec:
        return spec
    else:
        return ""
