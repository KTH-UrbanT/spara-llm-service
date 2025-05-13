import re
import requests
import os
import logging

class Spec:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

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
    
    def update_address(self, prompt):
        pattern = re.compile(r"\b(?:bor\ på|address\ är|live\ at|live\ in|address\ is|live\ on)\b\W+(\w+(?:\W+\w+){0,1})", re.IGNORECASE)
        address = pattern.findall(prompt)
        if address and address != self.address:
            host = os.getenv("ODEN_API_host")
            port = os.getenv("ODEN_API_port")
            logging.debug(address[0])
            req = f"http://{host}:{port}/api/v1/buildings/single_filter?filter_name=epc_idadr&filter_value={address[0]}" #change .env
            result = requests.get(req)
            if result.status_code == 200:
                logging.info("Status from API: 200")
                spec = Spec(**result.json()[0])
                self.address = address
                self.building = spec
                print(address[0])
            else:
                status = result.status_code
                error = result.reason
                logging.info(f"API failed with: {status}, {error}")
                self.building = None
                self.address = "ingen address info"
        if self.building:
            return self.building.__str__()
        return self.address

            

       

    building: Spec
    address: str 



def search_for_address(prompt, buildings):
    pattern = re.compile(r"\b(?:bor\ på|address\ är|live\ at|live\ in|address\ is)\b\W+(\w+(?:\W+\w+){0,1})", re.IGNORECASE)
    address = pattern.findall(prompt)

    if address:
        req = f"http://:8001/buildings/single_filter?filter_name=epc_idadr&filter_value={address[0]}"
        result = requests.get(req)
        print(result.text)
        print(address[0])
        return result.text
    else:
        print("No address found.")
    return None


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
