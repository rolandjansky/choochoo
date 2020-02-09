import React from 'react';
import TextField from "@material-ui/core/TextField";
import useWriter from "../../../workers/useWriter";


export default function EditField(props) {

    const {json, writer} = props;
    const [value, handleChange] = useWriter(json, writer);

    return <TextField label={props.json.label} value={value} onChange={handleChange} fullWidth variant="filled"/>;
}
