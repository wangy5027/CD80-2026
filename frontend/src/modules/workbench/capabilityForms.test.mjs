import test from 'node:test';
import assert from 'node:assert/strict';
import { emptyValue, fieldErrors, formSupported } from './capabilityForms.js';
const schema = { type:'object', properties:{ values:{type:'array', minItems:1, items:{type:'string', minLength:1}}, options:{type:'object', properties:{round:{type:'integer',minimum:0}},required:['round'],additionalProperties:false} }, required:['values'], additionalProperties:false };
test('forms preserve decimal strings and optional objects', () => {
 assert.deepEqual(emptyValue(schema),{values:['']});
 assert.equal(formSupported(schema),true);
 const value={values:['000.10','0.20']}; assert.deepEqual(fieldErrors(schema,value),[]); assert.equal(value.values[0],'000.10');
 assert.deepEqual(fieldErrors(schema,{values:[2]})[0].path,['values',0]);
 assert.deepEqual(fieldErrors(schema,{values:[]})[0].path,['values']);
 assert.deepEqual(fieldErrors(schema,{values:['1'],options:{round:-1}})[0].path,['options','round']);
});
test('unsupported schemas stay in advanced mode instead of dropping data', () => {
 assert.equal(formSupported({type:'object',additionalProperties:true}),false);
 assert.equal(formSupported({oneOf:[{type:'string'}]}),false);
 assert.equal(formSupported(undefined),false);
});
test('required, enum, booleans and scalar result types are validated', () => {
 assert.deepEqual(fieldErrors(schema,{})[0].path,['values']);
 assert.ok(fieldErrors({type:'integer'},1.5).length);
 assert.ok(fieldErrors({type:'boolean'},'false').length);
 assert.ok(fieldErrors({type:'string',enum:['a']},'b').length);
 assert.deepEqual(emptyValue({type:'null'}),null);
 assert.deepEqual(emptyValue({type:'string',const:'fixed'}),'fixed');
});
