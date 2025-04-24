import re

class Spec:
    def __init__(self, address, fnr, consumption, year):
        self.address = address
        self.fnr = fnr
        self.energy_consumption = consumption
        self.building_year = year

    def __str__(self):
        return f" Address: {self.address}, FNR: {self.fnr}, Energikonsumtion: {self.energy_consumption}, Byggnadsår: {self.building_year}"
        
    building_id: str
    fnr: str
    address: str
    buildingUUID: str
    deso: str
    building_year: int
    building_type: int
    atemp: float
    number_of_floors: float
    number_of_staircase: int
    energy_consumption: str


def search_for_address(prompt, buildings):
    address = ""
    pattern = re.compile(r"\b(?:bor\ på|address\ är)\b\W+(\w+(?:\W+\w+){0,1})", re.IGNORECASE)
    address = pattern.findall(prompt)
    print(address[0])
    return next((spec for spec in buildings if getattr(spec, "address", None).lower() == address[0].lower()), None)
    

    
        
        




def main(prompt):
    spec1 = Spec("AddressGatan 5", "F1N2R3", "60KWh", 1998)
    spec2 = Spec("blåkulla 3b", "F1N2R3", "80KWh", 1912)
    spec3 = Spec("Vårvägen 77", "F1N2R3", "70KWh", 2003)
    buildings = [spec1, spec2, spec3]
    spec = search_for_address(prompt, buildings)
    #print(spec)
    return spec.__str__()

