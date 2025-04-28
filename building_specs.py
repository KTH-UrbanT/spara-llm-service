import re
import pandas as pd
class Spec:
    def __init__(self, address, el_calc, varme_calc, basebent, stairs):
        self.address = address
        self.el_calc = el_calc
        self.varm_calc = varme_calc
        self.basement = basebent
        self.stairs = stairs

    def __str__(self):
        return f" Address: {self.address}, Elkonsumtion: {self.el_calc}, Värmekonsumtion: {self.varm_calc}, Källarplan: {self.basement}, Antal Trapphus: {self.stairs}"
        
    building_id: str
    fnr: str
    address: str
    buildingUUID: str
    deso: str
    building_year: int
    building_type: int
    atemp: float
    stairs: float
    stairs: int
    el_calc: int
    varm_calc: int


def search_for_address(prompt, buildings):
    pattern = re.compile(r"\b(?:bor\ på|address\ är)\b\W+(\w+(?:\W+\w+){0,1})", re.IGNORECASE)
    address = pattern.findall(prompt)

    if address:
        print(address[0])
        return next((spec for spec in buildings if getattr(spec, "address", None).lower() == address[0].lower()), None)
    else:
        print("No address found.")
    return None

    

    
        
        




def main(prompt):
    df = pd.read_csv("buildings.csv", sep=";", usecols=["IdAdr", "El_calc", "EgiVarme_calc", "EgenAntalKallarplan", "EgenAntalPlan", "EgenAntalTrapphus"], skipinitialspace=True)
    buildings = []
    for _, row in df.iterrows():
        spec = Spec(
            address=row["IdAdr"],
            el_calc=row["El_calc"],
            varme_calc=row["EgiVarme_calc"],
            basebent=row["EgenAntalKallarplan"],
            stairs=row["EgenAntalTrapphus"]
        )
        buildings.append(spec)
    #spec1 = Spec("AddressGatan 5", "F1N2R3", "60KWh", 1998)
    #spec2 = Spec("blåkulla 3b", "F1N2R3", "80KWh", 1912)
    #spec3 = Spec("Vårvägen 77", "F1N2R3", "70KWh", 2003)
    print(df)
    spec = search_for_address(prompt, buildings)
    #print(spec)
    return spec.__str__()

