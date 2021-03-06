import {ColumnCard, ColumnList, Layout, Loading} from "../../elements";
import React, {useEffect, useState} from "react";
import {Grid, Typography} from "@material-ui/core";
import {makeStyles} from "@material-ui/core/styles";
import StatisticsValues from "./elements/StatisticsValues";
import {handleJson} from "../../functions";


const useStyles = makeStyles(theme => ({
    right: {
        textAlign: 'right',
    },
    center: {
        textAlign: 'center',
    },
    left: {
        textAlign: 'left',
    },
    h3: {
        marginTop: theme.spacing(2),
    },
}));


function ModelStatistics(props) {

    const {model} = props;
    const classes = useStyles();
    const have_statistics = 'statistics' in model;
    const n = have_statistics ? model['statistics'][0]['n'] : 0;

    return (<>
        <Grid item xs={12} className={classes.h3}>
            <Typography variant='h3'>{model.name} {n ? `/ ${n}` : ''}</Typography>
        </Grid>
        {have_statistics && <StatisticsValues statistics={model.statistics}/>}
    </>);
}


function ComponentStatistics(props) {
    const {component} = props;
    return (<ColumnCard header={component.name}>
        {component.models.map((model, i) => <ModelStatistics model={model} key={i}/>)}
    </ColumnCard>);
}


function Columns(props) {

    const {components} = props;

    if (components === null) {
        return <Loading/>;  // undefined initial data
    } else {
        return (<ColumnList>
            {components.map((component, i) => <ComponentStatistics component={component} key={i}/>)}
        </ColumnList>);
    }
}


export default function Statistics(props) {

    const {history} = props;
    const [components, setComponents] = useState(null);
    const busyState = useState(null);
    const errorState = useState(null);
    const [error, setError] = errorState;
    const [reads, setReads] = useState(0);

    function reload() {
        setReads(reads + 1);
    }

    useEffect(() => {
        setComponents(null);
        fetch('/api/kit/statistics')
            .then(handleJson(history, setComponents, setError, busyState));
    }, [reads]);

    return (
        <Layout title='Kit Statistics'
                content={<Columns components={components}/>}
                reload={reload} busyState={busyState} errorState={errorState}/>
    );
}
